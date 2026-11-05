import { api } from './http';

/* ------------------------------------------------------------------
 * 标准 RESTful 资源工厂
 *
 * 后端约定（所有资源一致，便于前端批量生成）：
 *   GET    /resources            列表（分页 / 筛选走 query）
 *   GET    /resources/{id}       详情
 *   POST   /resources            新建
 *   PUT    /resources/{id}       全量替换
 *   PATCH  /resources/{id}       局部更新
 *   DELETE /resources/{id}       删除单条
 *   POST   /resources/{id}/{act} 领域动作（retry / approve / ...）
 *
 * 目的不是「省几行代码」，而是让所有资源的行为、错误、参数序列化保持一致；
 * 后端换字段名时只改实体自己的 types，不会散落到页面里。
 * ------------------------------------------------------------------ */

export interface PageResult<T> {
  items: T[];
  total: number;
  page?: number;
  pageSize?: number;
}

export interface RestResource<
  T,
  TList = PageResult<T>,
  TParams extends object = Record<string, unknown>,
> {
  /** GET /resources */
  list: (params?: TParams) => Promise<TList>;
  /** GET /resources/{id} */
  detail: (id: string) => Promise<T>;
  /** POST /resources */
  create: (body: Partial<T>) => Promise<T>;
  /** PUT /resources/{id} */
  replace: (id: string, body: Partial<T>) => Promise<T>;
  /** PATCH /resources/{id} */
  update: (id: string, body: Partial<T>) => Promise<T>;
  /** DELETE /resources/{id} */
  remove: (id: string) => Promise<{ deleted: number }>;
  /** POST /resources/{id}/{action} */
  action: (id: string, name: string, body?: unknown) => Promise<T>;
}

export function createRestResource<
  T,
  TList = PageResult<T>,
  TParams extends object = Record<string, unknown>,
>(base: string): RestResource<T, TList, TParams> {
  return {
    list: (params) => api.get<TList>(base, params as Record<string, unknown> | undefined),
    detail: (id) => api.get<T>(`${base}/${id}`),
    create: (body) => api.post<T>(base, body),
    replace: (id, body) => api.put<T>(`${base}/${id}`, body),
    update: (id, body) => api.patch<T>(`${base}/${id}`, body),
    remove: (id) => api.delete<{ deleted: number }>(`${base}/${id}`),
    action: (id, name, body) => api.post<T>(`${base}/${id}/${name}`, body),
  };
}

/**
 * 查询键工厂：每个资源一个命名空间。
 * 约定第一段永远是资源名，这样按资源整体失效不会误伤别人。
 *
 * @example
 * export const userKeys = createQueryKeys<UserListParams>('users');
 * userKeys.list({ page: 1 }) // ['users', 'list', { page: 1 }]
 */
export function createQueryKeys<TParams extends object = Record<string, unknown>>(resource: string) {
  const all = [resource] as const;

  return {
    all,
    lists: () => [...all, 'list'] as const,
    list: (params: TParams) => [...all, 'list', params] as const,
    details: () => [...all, 'detail'] as const,
    detail: (id: string) => [...all, 'detail', id] as const,
    stats: () => [...all, 'stats'] as const,
  };
}
