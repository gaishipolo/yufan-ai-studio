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
  /** 请求头（键统一小写）。鉴权 mock 靠它读 Authorization，其余 mock 可忽略 */
  headers: Record<string, string>;
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
    /** 与后端 `AppError.details` 对齐：同码不同处置时用（如 canRevokeAndDelete） */
    readonly details?: unknown,
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

/** 归一化请求头：AxiosHeaders 实例走 toJSON，普通对象直接用；键统一小写 */
export function readHeaders(config: AxiosRequestConfig): Record<string, string> {
  const source = config.headers as unknown as
    | { toJSON?: () => Record<string, unknown> }
    | Record<string, unknown>
    | undefined;

  const plain =
    (typeof source === 'object' && source && 'toJSON' in source && typeof source.toJSON === 'function'
      ? source.toJSON()
      : (source as Record<string, unknown> | undefined)) ?? {};

  const result: Record<string, string> = {};
  for (const [key, value] of Object.entries(plain)) {
    if (typeof value === 'string') result[key.toLowerCase()] = value;
  }
  return result;
}

export const jsonHeaders = () => new AxiosHeaders({ 'content-type': 'application/json' });
