import { AxiosHeaders, type AxiosRequestConfig } from 'axios';

/* ------------------------------------------------------------------
 * Mock 内核：请求解析、延迟、错误、响应包装
 * 业务 mock 只写路由表和 handler，实现细节都在这里
 * ------------------------------------------------------------------ */

export type MockMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

export interface MockContext {
  method: MockMethod;
  /** 去掉 baseURL 与 query 的路径，如 /tasks/T001 */
  path: string;
  /** query 参数 */
  params: Record<string, unknown>;
  /** JSON body（已解析） */
  body: Record<string, unknown>;
}

export interface MockRoute {
  method: MockMethod;
  /** 对 ctx.path 做整体匹配，按注册顺序取第一条命中的 */
  pattern: RegExp;
  handler: (ctx: MockContext) => unknown;
  /** 成功状态码，默认 200；新建一般是 201 */
  status?: number;
}

/** 业务异常：会被 adapter 转换成带状态码的 HTTP 错误 */
export class HttpError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = 'HttpError';
  }
}

/** 与后端 { code, message, data } 包装保持一致 */
export const ok = <T>(data: T) => ({ code: 0, message: 'OK', data });

const LATENCY_MIN = 120;
const LATENCY_MAX = 320;

export const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export const randomLatency = () =>
  LATENCY_MIN + Math.round(Math.random() * (LATENCY_MAX - LATENCY_MIN));

const BASE = import.meta.env.VITE_API_BASE_URL || '/api/v1';

export function pathOf(rawUrl: string): string {
  let path = rawUrl;
  if (/^https?:/i.test(path)) {
    try {
      path = new URL(path).pathname;
    } catch {
      path = rawUrl;
    }
  }
  path = path.split('?')[0];
  if (BASE !== '/' && path.startsWith(BASE)) path = path.slice(BASE.length);
  return path || '/';
}

export function readBody(config: AxiosRequestConfig): Record<string, unknown> {
  if (typeof config.data === 'string' && config.data) {
    try {
      return JSON.parse(config.data) as Record<string, unknown>;
    } catch {
      return {};
    }
  }
  if (config.data && typeof config.data === 'object') {
    return config.data as Record<string, unknown>;
  }
  return {};
}

export const jsonHeaders = () => new AxiosHeaders({ 'content-type': 'application/json' });
