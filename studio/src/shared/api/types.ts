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

export class ApiError extends Error {
  readonly code: number;

  constructor(message: string, code: number = -1) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
  }
}
