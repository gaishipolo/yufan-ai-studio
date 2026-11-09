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
  name: (id) => `/assets/${id}/name`,
  sharing: (id) => `/assets/${id}/sharing`,
  deleted: (id) => `/assets/${id}/deleted`,
};

export const archiveApi = {
  list: (category: ArchiveCategory | 'all', params: ArchiveSearch = {}) => {
    const {tagIds, categoryId, ...rest} = params;
    return api.get<ArchiveList>('/assets', {
      ...rest,
      // 新分类树按 id 过滤；**只在没有 id 时才带旧枚举**。
      // 两个都带的话后端以 id 为准，"全部素材"（id 为空）会退回旧枚举，
      // 结果是"点了全部却只看到原片"——而请求是 200，没人会去查。
      ...(categoryId ? {categoryId} : {category}),
      // 标签用逗号串成一个参数，**不能直接把数组交给 axios**：它的默认序列化是
      // `tagIds[]=a&tagIds[]=b`，而后端认的是 `tagIds`——结果是"筛选被静默忽略"，
      // 列表比用户以为的更宽，而请求本身是 200。这类错误只能靠人眼发现。
      ...(tagIds?.length ? {tagIds: tagIds.join(',')} : {}),
    });
  },
  detail: (id: string) => api.get<ArchivedAsset>(`/assets/${id}`),
  // 三个动作各写一条字面量路径：接口契约守卫按字面量比对后端 EndpointData，
  // 拼成 `${action}` 的模板串在守卫里只能落成占位符，等于没被覆盖。
  change: ({id, action, ...payload}: ArchiveChange & {id: string; action: 'name' | 'sharing' | 'deleted'}) =>
    api.put<ArchivedAsset>(CHANGE_PATH[action](id), payload),
  upload: ({file, category}: {file: File; category: ArchiveCategory}) =>
    api.post<ArchivedAsset>('/assets/upload', file, {params: {filename: file.name, category},
      headers: {'Content-Type': 'application/octet-stream'}, timeout: 600_000}),
  import: ({relativePath, category}: {relativePath: string; category: ArchiveCategory}) =>
    api.post<ArchivedAsset>('/assets/import', {relativePath, category}, {timeout: 120_000}),
  /** 预览：`/stream?v=hd-v2` 或原件。 */
  media: fetchBlob,
  /** 下载：只接受 `/file` 原件地址。 */
  download: fetchBlob,
};

export interface ArchiveSearch {
  scope?: 'mine' | 'public' | 'trash' | 'team';
  keyword?: string;
  state?: 'all' | 'private' | 'shared';
  page?: number;
  pageSize?: number;
  /** 当前选中的团队库；为空表示个人库或还没切库（团队额度显示 0） */
  teamId?: string;
  /** 受控标签，**AND 语义**：多项是「同时具备」，不是「任一」 */
  tagIds?: string[];
  /** 分类树上的位置（id）。传了就**不再**用旧的 `category` 枚举 */
  categoryId?: string;
  /** 二级媒体类型分面：`image` / `video` / `audio`，空表示不限 */
  mediaType?: string;
  /** `created_desc` / `created_asc` / `name_asc` */
  sort?: string;
}
