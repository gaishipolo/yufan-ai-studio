import axios, { type AxiosError } from 'axios';
import { API_BASE_URL } from '@/shared/config/app';
import { loadMockAdapter, USE_MOCK } from './mock-adapter-loader';
import { API_CODE_OK, ApiError, type ApiResponse } from './types';
import type { TokenOut } from './schema';

/* ------------------------------------------------------------------
 * 认证旁路通道：续期与登出
 *
 * 这两个请求**不能**走 `shared/api/http` 那条主干，两个原因各自独立：
 *
 *   1. 它们自己就可能 401。走主干会再次进入「401 → 试着续期」的分支，
 *      续期失败又 401，如此递归。所以旁路通道里没有 401 处理。
 *   2. 登出发生在「本地已经清场」之后（顺序见 `shared/access/end-session`），
 *      那时 store 里的 access 令牌已经是 null，主干的请求拦截器注不进鉴权头，
 *      服务端就无法知道该吊销哪一条会话。这里显式传令牌。
 *
 * 只做「发请求 + 解包」，错误照常抛 ApiError。怎么收（重试 / 放弃 / 不管）
 * 由调用方决定：续期失败要清场，登出失败不该把用户卡在页面上。
 * ------------------------------------------------------------------ */

const authHttp = axios.create({
  baseURL: API_BASE_URL,
  timeout: 20_000,
  headers: { 'Content-Type': 'application/json' },
});

/**
 * mock 模式下这两个请求也得走 mock 适配器，否则它们会绕过 mock 直接打真后端
 * （主干 `http` 的适配器不会传染到另一个 axios 实例）。
 * 漏了它的表现很隐蔽：mock 演示跑到 2 小时就再也续不上，看起来像后端挂了。
 *
 * 写在 `config.adapter` 上而不是 `authHttp.defaults.adapter`：axios 在拦截器之前
 * 就把 adapter 合进 config 了，改 defaults 只对下一个请求生效。
 */
authHttp.interceptors.request.use(async (config) => {
  if (USE_MOCK) config.adapter = await loadMockAdapter();
  return config;
});

/** 与主干同口径的错误归一：后端有 `{ code, message }` 就用它，否则退回 HTTP 状态 */
function toApiError(error: unknown): ApiError {
  const axiosError = error as AxiosError<ApiResponse<unknown>>;
  const payload = axiosError.response?.data;
  if (payload && typeof payload === 'object' && 'message' in payload) {
    return new ApiError(payload.message || '请求失败', payload.code ?? -1);
  }
  if (axiosError.code === 'ECONNABORTED') return new ApiError('请求超时，请稍后重试', 408);
  const status = axiosError.response?.status ?? -1;
  // 与 http.ts 同口径：服务没起来时别把 axios 的英文原文丢给用户
  if (status < 0) return new ApiError('无法连接后端服务，请确认服务已启动后重试', status);
  if (status >= 500) {
    return new ApiError(
      `后端服务暂时不可用（HTTP ${status}），请检查服务是否在运行或稍后重试`,
      status,
    );
  }
  return new ApiError(axiosError.message || '网络异常', status);
}

function unwrap<T>(body: ApiResponse<T> | undefined): T {
  if (!body || typeof body !== 'object') return body as unknown as T;
  if (body.code !== API_CODE_OK) throw new ApiError(body.message || '请求失败', body.code);
  return body.data;
}

/**
 * 用刷新令牌换一对新票（服务端会轮换：旧 refresh 立即作废）。
 *
 * 返回类型直接取生成物 `TokenOut`：后端加字段时这里不用改，
 * 也不会再出现「后端 6 个字段、前端只写 4 个」那种悄悄丢字段的情况。
 */
export async function requestRefresh(refreshToken: string): Promise<TokenOut> {
  try {
    const response = await authHttp.post<ApiResponse<TokenOut>>('/identity/refresh', {
      refreshToken,
    });
    return unwrap(response.data);
  } catch (error) {
    throw toApiError(error);
  }
}

/**
 * 作废服务端的那条会话（幂等：令牌已过期、已吊销都返回 200）。
 *
 * `accessToken` 必须由调用方在清场**之前**取好传进来。
 */
export async function requestLogout(accessToken: string): Promise<void> {
  try {
    await authHttp.post<ApiResponse<null>>('/identity/logout', undefined, {
      headers: { Authorization: `Bearer ${accessToken}` },
    });
  } catch (error) {
    throw toApiError(error);
  }
}
