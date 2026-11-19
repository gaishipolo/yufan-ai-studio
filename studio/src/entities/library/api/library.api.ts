import { api } from '@/shared/api/http';
import type {
  AssetPatchIn,
  BulkIn,
  BulkOut,
  DownloadIn,
  DownloadJobOut,
  DraftDecisionIn,
  LibraryAssetDetailOut,
  LibraryAssetKind,
  LibraryAssetOut,
  LibraryAssetPage,
  LibraryFacets,
  LibraryGroupOut,
  LibrarySelectionPage,
  LibraryTagOut,
  LibraryTagStatOut,
  PackOut,
  PackSummaryOut,
  ShareDecisionIn,
  ShareRequestIn,
  ShareRequestOut,
  TextAssetIn,
  TextAssetOut,
  TextAssetRestoreIn,
  TextAssetUpdateIn,
  TextAssetVersionOut,
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

/**
 * 选择器的筛选条件。
 *
 * `library` 与列表同一个枚举（`personal` / `group` / `team` / `all`）——模板
 * 工作台的「我的原片 / 公共原片」就是 `personal` / `team`，不另造 `scope`。
 * `origin` 用来只挑某一类来源：工作台的原片是 `template_source`（切换后新传）
 * 加 `imported`（并存期导入的存量），漏一个就少一半候选。
 */
export type LibrarySelectionParams = {
  kind?: string;
  tagIds?: string[];
  keyword?: string;
  page?: number;
  pageSize?: number;
  library?: string;
  origin?: string[];
};

/** 视图在 API 层就翻译成 `state` / `library`，不让调用方各拼一遍 */
export function toRequestParams(params: LibraryListParams): Record<string, unknown> {
  const { view, ...rest } = params;
  return { ...viewQuery(view), ...rest };
}

/**
 * 标签栏的计数范围：与列表同一个视图，避免「写 8 点进去只有 3 条」。
 *
 * `kind` 是可选的**类别**收窄：悬浮栏按「图片 / 成片 / 音频 / 文本」分类
 * 浏览时，胶囊上的数字必须也是这一类里的条数。不传就是全类计数
 * （资产库页的标签栏就是全类，它不按类别切）。
 *
 * `tagIds` 是可选的**候选范围**：只统计符合这组标签的素材。
 * 模板作者圈了候选集之后，模板库要显示的是「这批素材身上实际有的二级标签」
 * —— 按全库算会列出一堆这批素材一个都没挂的标签，用户点了只得到空列表。
 */
export type LibraryTagScope = ReturnType<typeof viewQuery> & {
  kind?: LibraryAssetKind;
  tagIds?: string[];
};

export const libraryApi = {
  list: (params: LibraryListParams) =>
    api.get<LibraryAssetPage>(`/library/assets`, toRequestParams(params), {
      paramsSerializer: REPEATED_ARRAY,
    }),

  facets: () => api.get<LibraryFacets>(`/library/facets`),

  selection: (params: LibrarySelectionParams) =>
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

  /**
   * 悬浮大图（长边 1280 的派生 JPEG）。
   *
   * 不用 `/file`：原图平均 2.6MB、最大 9.5MB，在网格里来回悬停
   * 就是上百 MB。不用 `/thumbnail`：那只有 360×240，几十像素看不清细节。
   */
  large: (id: string) =>
    api.get<Blob>(`/library/assets/${id}/large`, undefined, {
      responseType: 'blob',
      timeout: 600_000,
    }),

  /**
   * 设置用途：`case` = 移入案例池，空串 = 移回普通素材。
   *
   * 独立于 `patch`：改用途会改变这条素材的可见范围（案例对所有人可读），
   * 不该混在改名请求里顺手做掉。
   */
  setPurpose: (id: string, purpose: string, expectedVersion = 0) =>
    api.post<LibraryAssetOut>(`/library/assets/${id}/purpose`, {
      purpose,
      expectedVersion,
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
    kind: 'image' | 'audio' | 'video' | 'text',
    options?: { sourceKey?: string; onProgress?: (ratio: number) => void; purpose?: string },
  ) =>
    api.post<LibraryAssetOut>(
      `/library/assets/upload`,
      file,
      {
        params: {
          filename: file.name,
          kind,
          sourceKey: options?.sourceKey,
          ...(options?.purpose ? { purpose: options.purpose } : {}),
        },
        headers: { 'Content-Type': 'application/octet-stream' },
        timeout: 600_000,
        onUploadProgress: (event) => {
          if (!options?.onProgress) return;
          const ratio = event.total ? event.loaded / event.total : 0;
          options.onProgress(Math.min(1, ratio));
        },
      },
    ),

  /**
   * 上传模板库的原片视频。
   *
   * 与 `upload` 分开的不是「类型不同」而是**来源不同**：这条走后端
   * `/library/source-videos`，落库时 `origin=template_source`，模板库随后靠它
   * 只挑原片；混进通用上传会让来源变成前端传参，写错没人发现。
   */
  uploadSourceVideo: (file: File, options?: {
    sourceKey?: string;
    onProgress?: (ratio: number) => void;
    purpose?: string;
  }) =>
    api.post<LibraryAssetOut>(
      `/library/source-videos`,
      file,
      {
        params: {
          filename: file.name,
          sourceKey: options?.sourceKey,
          ...(options?.purpose ? { purpose: options.purpose } : {}),
        },
        headers: { 'Content-Type': 'application/octet-stream' },
        timeout: 600_000,
        onUploadProgress: (event) => {
          if (!options?.onProgress) return;
          const ratio = event.total ? event.loaded / event.total : 0;
          options.onProgress(Math.min(1, ratio));
        },
      },
    ),

  /* ------------------------------------------------------------ 文本资产 */
  /** 站内新建：直接写正文，不传文件。extension 只认 md / txt。 */
  createTextAsset: (body: TextAssetIn) =>
    api.post<TextAssetOut>(`/library/text-assets`, body),

  /** 读正文与当前内容版本（列表里只有 textExcerpt，正文要单独取）。 */
  readText: (id: string) => api.get<TextAssetOut>(`/library/assets/${id}/text`),

  /** 在线编辑：带 expectedVersion 防并发覆盖，409 由调用方给明确提示。 */
  updateText: (id: string, body: TextAssetUpdateIn) =>
    api.put<TextAssetOut>(`/library/assets/${id}/text`, body),

  /** 版本历史：默认不带正文，按版本读时才带。 */
  listTextVersions: (id: string) =>
    api.get<TextAssetVersionOut[]>(`/library/assets/${id}/text/versions`),

  /** 读取某一历史版本的正文。 */
  readTextVersion: (id: string, version: number) =>
    api.get<TextAssetVersionOut>(`/library/assets/${id}/text/versions/${version}`),

  /** 恢复历史版本：服务端写成新的一版，不改历史行。 */
  restoreTextVersion: (id: string, body: TextAssetRestoreIn) =>
    api.post<TextAssetOut>(`/library/assets/${id}/text/restore`, body),

  /** 纯文本取件（text/plain + nosniff）：禁止 axios 把响应当 JSON 解析。 */
  rawText: (id: string) =>
    api.get<string>(`/library/assets/${id}/text/raw`, undefined, {
      responseType: 'text',
      transformResponse: [(data: unknown) => data],
    }),

  approveDraft: (id: string, body: DraftDecisionIn) =>
    api.post<LibraryAssetDetailOut>(`/library/drafts/${id}/approve`, body),

  rejectDraft: (id: string, body: DraftDecisionIn) =>
    api.post<LibraryAssetDetailOut>(`/library/drafts/${id}/reject`, body),

  restore: (id: string) => api.post<LibraryAssetDetailOut>(`/library/trash/${id}/restore`),

  purge: (id: string) => api.delete<null>(`/library/trash/${id}`),

  bulk: (body: BulkIn) => api.post<BulkOut>(`/library/assets/bulk`, body),

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

  /** 标签词表 + 当前入口下每个标签的素材数（按添加时间排序由后端保证）。 */
  tags: (scope: LibraryTagScope) =>
    api.get<LibraryTagStatOut[]>(`/library/tags`, scope, {
      paramsSerializer: REPEATED_ARRAY,
    }),

  /** `parentId` 给一级的 id 就建成二级；留空建成一级。 */
  /** 封装列表：`tagIds` 传槽位的候选标签，只返回「成员标签与之有交集」的包 */
  packs: (params: {tagIds?: string[]; keyword?: string}) =>
    api.get<PackSummaryOut[]>(`/library/packs`, params, {
      paramsSerializer: REPEATED_ARRAY,
    }),

  pack: (id: string) => api.get<PackOut>(`/library/packs/${id}`),

  /** 改封装：只传要改的那个字段（`undefined` = 不动） */
  updatePack: (id: string, body: {name?: string; assetIds?: string[]}) =>
    api.patch<PackOut>(`/library/packs/${id}`, body),

  /** 建封装：把一组素材打成一个包（成员顺序就是包内顺序） */
  createPack: (body: {name: string; assetIds: string[]}) =>
    api.post<PackOut>(`/library/packs`, {name: body.name, assetIds: body.assetIds}),

  createTag: (name: string, parentId?: string) =>
    api.post<LibraryTagOut>(`/library/tags`, { name, parentId: parentId ?? null }),

  /** 这三个是 `library:tag:manage`：词表结构由管理员维护，打标入口不出现它们 */
  renameTag: (id: string, name: string) =>
    api.patch<LibraryTagOut>(`/library/tags/${id}`, { name }),

  moveTag: (id: string, parentId: string | null) =>
    api.patch<LibraryTagOut>(`/library/tags/${id}/parent`, { parentId }),

  deleteTag: (id: string) => api.delete<null>(`/library/tags/${id}`),
};

export type { LibrarySelectionPage };
