import axios, { type AxiosError, type AxiosRequestConfig } from 'axios';
import { mockAdapter } from '@/mocks/adapter';
import { API_CODE_OK, ApiError, type ApiResponse } from './types';

export const http = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api/v1',
  timeout: 20_000,
  headers: { 'Content-Type': 'application/json' },
});

/* ------------------------------------------------------------------
 * 数据源切换：唯一开关在 .env 的 VITE_USE_MOCK
 * - true  → 走内置 mock adapter（模拟真实 REST 行为）
 * - false → 走真实后端（dev 环境由 vite proxy 转发到 VITE_API_PROXY_TARGET）
 * ------------------------------------------------------------------ */
if (import.meta.env.VITE_USE_MOCK === 'true') {
  http.defaults.adapter = mockAdapter;
}

/** 请求拦截：注入鉴权头（后续接登录后启用） */
http.interceptors.request.use((config) => {
  const token = localStorage.getItem('sf_token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

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
    const payload = error.response?.data;
    if (payload && typeof payload === 'object' && 'message' in payload) {
      return Promise.reject(new ApiError(payload.message, payload.code ?? error.response?.status ?? -1));
    }
    if (error.code === 'ECONNABORTED') {
      return Promise.reject(new ApiError('请求超时，请稍后重试', 408));
    }
    return Promise.reject(new ApiError(error.message || '网络异常', error.response?.status ?? -1));
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
