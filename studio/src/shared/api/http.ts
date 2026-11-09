import axios, {
  type AxiosError,
  type AxiosRequestConfig,
  type InternalAxiosRequestConfig,
} from 'axios';
import { endSession } from '@/shared/access/end-session';
import { refreshAccessToken } from '@/shared/access/token-refresh';
import { useSessionStore } from '@/shared/access/session';
import { API_BASE_URL } from '@/shared/config/app';
import { loadMockAdapter, USE_MOCK } from './mock-adapter-loader';
import { API_CODE_OK, ApiError, type ApiResponse } from './types';

export const http = axios.create({
  baseURL: API_BASE_URL,
  timeout: 20_000,
  headers: { 'Content-Type': 'application/json' },
});

/* ------------------------------------------------------------------
 * 数据源切换：唯一开关在 .env 的 VITE_USE_MOCK
 * - true  → 走内置 mock adapter（模拟真实 REST 行为）
 * - false → 走真实后端（dev 环境由 vite proxy 转发到 VITE_API_PROXY_TARGET）
 *
 * 适配器由 `mock-adapter-loader` 按需动态加载，原因写在那个文件里：
 * 静态 import 会让 rollup 保留这条副作用导入，入口包白白多出约 22 kB，
 * 而且 `admin@12345` 之类的演示口令会出现在生产 JS 里。
 *
 * 请求拦截器里 await 一次，保证第一个请求也不会跑在适配器就位之前；
 * 模块只加载一次，后续请求 await 的是已 resolve 的同一个 Promise。
 * ------------------------------------------------------------------ */
http.interceptors.request.use(async (config) => {
  /* 挂 mock 适配器必须写在 **config 上**，不能只写 `http.defaults.adapter`：
     axios 在进入拦截器链之前就已经把 `adapter` 合进 config 了，
     改 defaults 只对**下一个**请求生效 —— 表现是 mock 模式第一个请求直接打到真网络
     （页面上就是「登录时好时坏」，取决于谁先发请求）。 */
  if (USE_MOCK) config.adapter = await loadMockAdapter();

  const token = useSessionStore.getState().token;
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

/** 登录接口本身返回 401 时不能当成「会话失效」，否则密码输错会连带清场 */
const AUTH_ENTRY_PATHS = ['/identity/login'];

/**
 * 是否是「认证入口」请求。**精确比对路径**，不用 `includes`。
 *
 * `url.includes('/identity/login')` 现在碰巧只命中登录接口，但后端只要加一个
 * `/identity/login-audit`，它的 401 就会被当成「密码输错」，既不续期也不报错，
 * 直接清场。查询串要剥掉（`?t=` 之类的缓存穿透参数），哈希同理。
 */
function isAuthEntryRequest(url?: string) {
  if (!url) return false;
  const path = url.split('?')[0]?.split('#')[0] ?? '';
  return AUTH_ENTRY_PATHS.some((entry) => path === entry || path.endsWith(entry));
}

/** 原请求已经重放过一次就不再重放：续期失败时第二次 401 必须落地为清场 */
type RetriableConfig = InternalAxiosRequestConfig & { _retried?: boolean };

/**
 * 401 的两级处理：**先续期，再重放原请求；续不上才清场**。
 *
 * 为什么不直接清场：后端 access 令牌只有 2 小时，而会话有 7 天并滑动续期。
 * 一律清场等于「每个用户干满 2 小时被踢一次」，而服务端本来愿意让他继续待着。
 *
 * 原请求必须**重放**而不是把 401 抛给调用方：调用方（页面）不认识「令牌刚过期」，
 * 它只会显示错误态，用户看到的是「列表加载失败」而不是「稍等，已自动续期」。
 *
 * 重放走的是同一个 `http` 实例，因此会再进一次请求拦截器——那时 store 里已是新令牌，
 * 鉴权头自然正确；这里额外显式写一次头，是为了让重放不依赖 store 的读取时机。
 *
 * 注意两次失败的性质不同，不能共用一个 catch：
 *   - 续期失败       → 会话真的结束了，清场；
 *   - 重放又失败     → 那是业务请求本身的问题（500 / 404），原样抛给调用方。
 * 混在一起会把「重放时 500」错报成「登录失效」，用户被无故踢下线。
 */
async function finishUnauthorized(
  error: AxiosError<ApiResponse<unknown>>,
  config: RetriableConfig | undefined,
): Promise<unknown> {
  if (config && !config._retried && !isAuthEntryRequest(config.url)) {
    let token: string | null = null;
    try {
      token = await refreshAccessToken();
    } catch {
      token = null; // 续不上：下面的清场才是结论
    }
    if (token) {
      config._retried = true;
      config.headers.Authorization = `Bearer ${token}`;
      return http.request(config);
    }
  }

  handleUnauthorized(config?.url);
  throw toApiError(error);
}

/**
 * 401 的最终出口：结束会话（清会话 + 清查询缓存）。
 * 跳转不在这里做——会话变 anonymous 后 <RequireAuth> 会自然重定向，
 * 这样「守卫」只有一处真相，也不会出现拦截器与路由互相打架。
 */
function handleUnauthorized(url?: string) {
  if (isAuthEntryRequest(url)) return;
  if (useSessionStore.getState().status === 'anonymous') return;
  // 带上原因：登录页据此提示「登录状态已失效」，与用户主动退出区分开
  endSession('unauthorized');
}

/** 错误归一：后端有 `{ code, message }` 就用它，否则退回 HTTP 状态码 */
function toApiError(error: AxiosError<ApiResponse<unknown>>) {
  const payload = error.response?.data;
  if (payload && typeof payload === 'object' && 'message' in payload) {
    return new ApiError(payload.message, payload.code ?? error.response?.status ?? -1);
  }
  if (error.code === 'ECONNABORTED') return new ApiError('请求超时，请稍后重试', 408);
  const status = error.response?.status ?? -1;
  /* 后端进程没起来时，dev 代理/axios 只给 "Request failed with status code 500"
     这类英文原文。直接展示会让人以为接口写错了，其实只是服务没在运行。 */
  if (status < 0) return new ApiError('无法连接后端服务，请确认服务已启动后重试', status);
  if (status >= 500) {
    return new ApiError(
      `后端服务暂时不可用（HTTP ${status}），请检查服务是否在运行或稍后重试`,
      status,
    );
  }
  return new ApiError(error.message || '网络异常', status);
}

/** 响应拦截：解包 { code, message, data }，统一抛 ApiError */
http.interceptors.response.use(
  (response) => {
    const body = response.data as ApiResponse<unknown> | undefined;
    if (body && typeof body === 'object' && 'code' in body) {
      if (body.code !== API_CODE_OK) {
        throw new ApiError(body.message || '请求失败', body.code);
      }
      response.data = body.data;
    }
    return response;
  },
  (error: AxiosError<ApiResponse<unknown>>) => {
    const config = error.config as RetriableConfig | undefined;

    // 登录接口的 401 是「密码错了」，不是「会话失效」，不能拿它去续期
    if (error.response?.status === 401 && !isAuthEntryRequest(config?.url)) {
      return finishUnauthorized(error, config);
    }

    return Promise.reject(toApiError(error));
  },
);

type Params = Record<string, unknown> | undefined;

/** 类型安全的轻量 API 客户端（拦截器已解包 data） */
export const api = {
  get: <T>(url: string, params?: Params, config?: AxiosRequestConfig) =>
    http.get<T>(url, { params, ...config }).then((r) => r.data),

  post: <T>(url: string, body?: unknown, config?: AxiosRequestConfig) =>
    http.post<T>(url, body, config).then((r) => r.data),

  put: <T>(url: string, body?: unknown) => http.put<T>(url, body).then((r) => r.data),

  patch: <T>(url: string, body?: unknown) => http.patch<T>(url, body).then((r) => r.data),

  delete: <T>(url: string, body?: unknown) =>
    http.delete<T>(url, { data: body }).then((r) => r.data),
};
