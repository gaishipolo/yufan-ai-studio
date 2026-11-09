import { api } from '@/shared/api/http';
import type {
  AssetPatchIn,
  BulkIn,
  BulkOut,
  DownloadIn,
  DownloadJobOut,
  DraftDecisionIn,
  LibraryAssetDetailOut,
  LibraryAssetOut,
  LibraryAssetPage,
  LibraryFacets,
  LibraryGroupOut,
  LibrarySelectionPage,
  LibraryTagOut,
  ShareDecisionIn,
  ShareRequestIn,
  ShareRequestOut,
} from '@/shared/api/schema';
import { viewQuery, type LibraryView } from '../model/types';

/** 数组查询参数必须序列化成 `tagIds=a&tagIds=b`：后端是 `Query(list[str])`，
 * axios 默认的 `tagIds[]=a` 它解析不到，会静默变成「不筛选」。 */
const REPEATED_ARRAY = { indexes: null } as const;

export interface LibraryListParams {
  view: LibraryView;
  kind?: string;
  tagIds?: string[];
  keyword?: string;
  sort?: string;
  page?: number;
  pageSize?: number;
}

/** 视图在 API 层就翻译成 `state` / `library`，不让调用方各拼一遍 */
export function toRequestParams(params: LibraryListParams): Record<string, unknown> {
  const { view, ...rest } = params;
  return { ...viewQuery(view), ...rest };
}

export const libraryApi = {
  list: (params: LibraryListParams) =>
    api.get<LibraryAssetPage>(`/library/assets`, toRequestParams(params), {
      paramsSerializer: REPEATED_ARRAY,
    }),

  facets: () => api.get<LibraryFacets>(`/library/facets`),

  selection: (params: Omit<LibraryListParams, 'view' | 'sort'> & { includePacks?: boolean }) =>
    api.get<LibrarySelectionPage>(`/library/selection`, params, {
      paramsSerializer: REPEATED_ARRAY,
    }),

  detail: (id: string) => api.get<LibraryAssetDetailOut>(`/library/assets/${id}`),

  /** 原文件字节。预览与下载共用同一个端点：v2 不做转码派流。 */
  file: (id: string) =>
    api.get<Blob>(`/library/assets/${id}/file`, undefined, {
      responseType: 'blob',
      timeout: 600_000,
    }),

  patch: (id: string, body: AssetPatchIn) =>
    api.patch<LibraryAssetDetailOut>(`/library/assets/${id}`, body),

  remove: (id: string, options?: { revokeOwnGroupShare?: boolean; reason?: string }) =>
    api.delete<null>(`/library/assets/${id}`, undefined, {
      params: options,
    }),

  /** 上传：文件字节直接做 body，文件名与类型走 query（与旧素材库同一套约定） */
  upload: (
    file: File,
    kind: 'image' | 'audio',
    options?: { sourceKey?: string; onProgress?: (ratio: number) => void },
  ) =>
    api.post<LibraryAssetOut>(
      `/library/assets/upload`,
      file,
      {
        params: { filename: file.name, kind, sourceKey: options?.sourceKey },
        headers: { 'Content-Type': 'application/octet-stream' },
        timeout: 600_000,
        onUploadProgress: (event) => {
          if (!options?.onProgress) return;
          const ratio = event.total ? event.loaded / event.total : 0;
          options.onProgress(Math.min(1, ratio));
        },
      },
    ),

  approveDraft: (id: string, body: DraftDecisionIn) =>
    api.post<LibraryAssetDetailOut>(`/library/drafts/${id}/approve`, body),

  rejectDraft: (id: string, body: DraftDecisionIn) =>
    api.post<LibraryAssetDetailOut>(`/library/drafts/${id}/reject`, body),

  restore: (id: string) => api.post<LibraryAssetDetailOut>(`/library/trash/${id}/restore`),

  purge: (id: string) => api.delete<null>(`/library/trash/${id}`),

  bulk: (body: BulkIn) => api.post<BulkOut>(`/library/assets/bulk`, body),

  createPack: (body: {
    name: string;
    assetIds?: string[];
    coverAssetId?: string;
    tagIds?: string[];
  }) => api.post<LibraryAssetDetailOut>(`/library/packs`, body),

  updatePack: (
    id: string,
    body: { name?: string; assetIds?: string[]; coverAssetId?: string; expectedVersion?: number },
  ) => api.patch<LibraryAssetDetailOut>(`/library/packs/${id}`, body),

  deletePack: (id: string) => api.delete<null>(`/library/packs/${id}`),

  createDownload: (body: DownloadIn) => api.post<DownloadJobOut>(`/library/downloads`, body),

  downloads: () => api.get<DownloadJobOut[]>(`/library/downloads`),

  download: (id: string) => api.get<DownloadJobOut>(`/library/downloads/${id}`),

  downloadFile: (id: string) =>
    api.get<Blob>(`/library/downloads/${id}/file`, undefined, {
      responseType: 'blob',
      timeout: 600_000,
    }),

  shareRequests: (scope: 'mine' | 'pending') =>
    api.get<ShareRequestOut[]>(`/library/share-requests`, { scope }),

  createShareRequest: (body: ShareRequestIn) =>
    api.post<ShareRequestOut>(`/library/share-requests`, body),

  approveShareRequest: (id: string, body: ShareDecisionIn) =>
    api.post<ShareRequestOut>(`/library/share-requests/${id}/approve`, body),

  rejectShareRequest: (id: string, body: ShareDecisionIn) =>
    api.post<ShareRequestOut>(`/library/share-requests/${id}/reject`, body),

  cancelShareRequest: (id: string, body?: ShareDecisionIn) =>
    api.post<ShareRequestOut>(`/library/share-requests/${id}/cancel`, body ?? {}),

  groups: () => api.get<LibraryGroupOut[]>(`/library/groups`),

  tags: () => api.get<LibraryTagOut[]>(`/library/tags`),

  createTag: (name: string) => api.post<LibraryTagOut>(`/library/tags`, { name }),
};

export type { LibrarySelectionPage };
