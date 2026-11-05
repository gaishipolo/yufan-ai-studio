/** 统一响应包装：后端所有接口都返回该结构 */
export interface ApiResponse<T> {
  code: number;
  message: string;
  data: T;
}

/** 分页响应 */
export interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  pageSize: number;
}

/** 分页请求参数 */
export interface PageParams {
  page?: number;
  pageSize?: number;
}

/** 业务错误码：0 表示成功 */
export const API_CODE_OK = 0;

/**
 * 从业务码还原 HTTP 语义。
 *
 * 后端约定 `code = http_status * 100 + 模块号 * 10 + 序号`（见 `core/exceptions.py`），
 * 因此 `code // 100` 恒等于 HTTP 状态码：40121 → 401、42900 → 429、50000 → 500。
 *
 * 前端**不要**按完整业务码分支——每个模块在自己的号段里编号（identity 的登录失败是
 * 40121 而不是框架的 40100），写死具体码会在换个模块或调整序号时静默失效。
 * 只按状态段判断，具体原因仍然看 `message`。
 *
 * mock 适配器不造业务码，直接透传 HTTP 状态码（401 / 429），这里一并兼容。
 */
export function errorHttpStatus(code: number): number {
  if (!Number.isFinite(code) || code < 1_000) return code;
  return Math.floor(code / 100);
}

export class ApiError extends Error {
  readonly code: number;

  constructor(message: string, code: number = -1) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
  }
}

/**
 * 这次失败是不是「登录态失效」。
 *
 * 401 有专门的处理路径（拦截器清场 + 守卫跳登录页），
 * 其余失败必须走错误态展示 —— 两者不能混为一谈，所以判定收在一处。
 */
export function isUnauthorizedError(error: unknown): boolean {
  return error instanceof ApiError && errorHttpStatus(error.code) === 401;
}
