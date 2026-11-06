import { api } from '@/shared/api/http';
import type { ArchivedAsset, ArchiveList, ArchiveChange } from '@/shared/api/schema';
import type { ArchiveCategory } from '../model/archive.types';

/**
 * 取归档文件的字节。
 *
 * 预览与下载在这里是同一个请求、不同的 URL：
 * 预览传 `/stream?v=hd-v2`（浏览器兼容的派生流）或图片/音频的原件，
 * 下载必须传 `/file` 原件。**传错地址不会报错**，只会让「下载原始文件」
 * 悄悄存下转码后的版本，所以调用点各自写清楚用途，别互相复用 Blob。
 *
 * 实现只留一份：`responseType` 与超时如果写两遍，改一处漏一处是迟早的事。
 */
function fetchBlob(url: string) {
  return api.get<Blob>(url.replace('/api/v1', ''), undefined, {responseType: 'blob', timeout: 600_000});
}

const CHANGE_PATH: Record<'name' | 'sharing' | 'deleted', (id: string) => string> = {
  name: (id) => `/assets/archive/${id}/name`,
  sharing: (id) => `/assets/archive/${id}/sharing`,
  deleted: (id) => `/assets/archive/${id}/deleted`,
};

export const archiveApi = {
  list: (category: ArchiveCategory | 'all', params: ArchiveSearch = {}) => api.get<ArchiveList>('/assets/archive', {category, ...params}),
  detail: (id: string) => api.get<ArchivedAsset>(`/assets/archive/${id}`),
  // 三个动作各写一条字面量路径：接口契约守卫按字面量比对后端 EndpointData，
  // 拼成 `${action}` 的模板串在守卫里只能落成占位符，等于没被覆盖。
  change: ({id, action, ...payload}: ArchiveChange & {id: string; action: 'name' | 'sharing' | 'deleted'}) =>
    api.put<ArchivedAsset>(CHANGE_PATH[action](id), payload),
  upload: ({file, category}: {file: File; category: ArchiveCategory}) =>
    api.post<ArchivedAsset>('/assets/archive/upload', file, {params: {filename: file.name, category},
      headers: {'Content-Type': 'application/octet-stream'}, timeout: 600_000}),
  import: ({relativePath, category}: {relativePath: string; category: ArchiveCategory}) =>
    api.post<ArchivedAsset>('/assets/archive/import', {relativePath, category}, {timeout: 120_000}),
  /** 预览：`/stream?v=hd-v2` 或原件。 */
  media: fetchBlob,
  /** 下载：只接受 `/file` 原件地址。 */
  download: fetchBlob,
};

export interface ArchiveSearch {
  scope?: 'mine' | 'public' | 'trash';
  keyword?: string;
  state?: 'all' | 'private' | 'shared';
  page?: number;
  pageSize?: number;
}
