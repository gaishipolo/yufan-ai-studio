import { useEffect, useState } from 'react';
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type {
  AssetPatchIn,
  BulkIn,
  DownloadIn,
  DraftDecisionIn,
  ShareDecisionIn,
  ShareRequestIn,
  TextAssetIn,
  TextAssetRestoreIn,
  TextAssetUpdateIn,
} from '@/shared/api/schema';
import { ApiError } from '@/shared/api/types';
import { saveBlob } from '@/shared/lib/download';
import { libraryApi, type LibraryListParams, type LibraryTagScope } from './library.api';

/**
 * 查询键第一段固定 `library`：并存期要能一次失效整块，且不能误伤
 * `archive-assets` / `packs` 那些旧库的缓存。
 */
export const libraryKeys = {
  all: ['library'] as const,
  lists: () => [...libraryKeys.all, 'list'] as const,
  list: (params: LibraryListParams) => [...libraryKeys.lists(), params] as const,
  facets: () => [...libraryKeys.all, 'facets'] as const,
  details: () => [...libraryKeys.all, 'detail'] as const,
  detail: (id: string) => [...libraryKeys.details(), id] as const,
  media: (id: string) => [...libraryKeys.all, 'media', id] as const,
  /** 前缀：切入口只是换了计数范围，任一次写操作都要把所有范围的缓存失效 */
  tags: () => [...libraryKeys.all, 'tags'] as const,
  tagList: (scope: LibraryTagScope) => [...libraryKeys.tags(), scope] as const,
  groups: () => [...libraryKeys.all, 'groups'] as const,
  downloads: () => [...libraryKeys.all, 'downloads'] as const,
  download: (id: string) => [...libraryKeys.all, 'download', id] as const,
  requests: (scope: 'mine' | 'pending') => [...libraryKeys.all, 'requests', scope] as const,
  selection: (params: object) => [...libraryKeys.all, 'selection', params] as const,
  /** 文本正文按资产隔离：编辑保存后只失效这一条，不惊动别处 */
  texts: (id: string) => [...libraryKeys.all, 'text', id] as const,
  textVersions: (id: string) => [...libraryKeys.all, 'textVersions', id] as const,
  textVersion: (id: string, version: number) =>
    [...libraryKeys.textVersions(id), version] as const,
};

export function useLibraryList(params: LibraryListParams) {
  return useQuery({
    queryKey: libraryKeys.list(params),
    queryFn: () => libraryApi.list(params),
    /* 切换视图时保留上一屏，避免整页闪白；角标与列表来自同一次响应 */
    placeholderData: (previous) => previous,
  });
}

export function useLibraryFacets() {
  return useQuery({
    queryKey: libraryKeys.facets(),
    queryFn: libraryApi.facets,
    staleTime: 30_000,
  });
}

export function useLibraryDetail(id: string | null) {
  return useQuery({
    queryKey: libraryKeys.detail(id ?? ''),
    queryFn: () => libraryApi.detail(id ?? ''),
    enabled: !!id,
    /* 30 秒内不重拉：详情里有就地改名与标签编辑，聚焦窗口时刷新会把输入冲掉 */
    staleTime: 30_000,
  });
}

/** 标签计数按入口变化，所以 scope 进 queryKey；不保留上一屏，避免个人/小组数字串味。 */
export function useLibraryTags(scope: LibraryTagScope) {
  return useQuery({
    queryKey: libraryKeys.tagList(scope),
    queryFn: () => libraryApi.tags(scope),
    staleTime: 30_000,
  });
}

/** 选择器：硬过滤 `state=active`，封装也不出现在候选里（禁嵌套） */
export function useLibrarySelection(params: {
  keyword?: string;
  kind?: string;
  tagIds?: string[];
  page?: number;
  pageSize?: number;
  /** 暂不需要这份候选时先不发请求（例如封装编辑器正在编辑旧素材库的包） */
  enabled?: boolean;
}) {
  const { enabled = true, ...rest } = params;
  return useQuery({
    queryKey: libraryKeys.selection(rest),
    queryFn: () => libraryApi.selection({ ...rest, includePacks: false }),
    placeholderData: (previous) => previous,
    enabled,
  });
}

/**
 * 侧栏悬浮选择器的数据源：滚动追加，不走「上一页 / 下一页」。
 *
 * 侧栏只有三百来像素宽，翻页会丢掉「刚才看到的那张在哪」，而拖拽恰恰依赖
 * 用户还看得见它。下一页由 `loaded >= total` 判定，不额外要 total_pages 字段。
 */
export function useLibrarySelectionInfinite(params: {
  keyword?: string;
  kind?: string;
  tagIds?: string[];
  pageSize?: number;
}) {
  return useInfiniteQuery({
    // 与 useLibrarySelection 共用 `selection` 前缀，但键里多一个 marks，避免命中缓存里
    // 那份「单页结构」的数据（两者形状不同，共用会让某一方读到 undefined）
    queryKey: libraryKeys.selection({ ...params, infinite: true }),
    queryFn: ({ pageParam }) =>
      libraryApi.selection({ ...params, page: pageParam, includePacks: false }),
    initialPageParam: 1,
    getNextPageParam: (last) => {
      const loaded = (last.page ?? 1) * (last.pageSize ?? 24);
      return loaded < (last.total ?? 0) ? (last.page ?? 1) + 1 : undefined;
    },
  });
}

export function useMyGroups() {
  return useQuery({ queryKey: libraryKeys.groups(), queryFn: libraryApi.groups, staleTime: 60_000 });
}

/** 列表 / 分面 / 详情 / 标签计数一起失效：打标与去标会同时改这四处 */
function useInvalidateLibrary() {
  const client = useQueryClient();
  return () => {
    void client.invalidateQueries({ queryKey: libraryKeys.lists() });
    void client.invalidateQueries({ queryKey: libraryKeys.facets() });
    void client.invalidateQueries({ queryKey: libraryKeys.details() });
    void client.invalidateQueries({ queryKey: libraryKeys.tags() });
  };
}

export function usePatchAsset() {
  const invalidate = useInvalidateLibrary();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: AssetPatchIn }) => libraryApi.patch(id, body),
    onSuccess: invalidate,
  });
}

export function useDeleteAsset() {
  const invalidate = useInvalidateLibrary();
  return useMutation({
    mutationFn: ({
      id,
      revokeOwnGroupShare,
      reason,
    }: {
      id: string;
      revokeOwnGroupShare?: boolean;
      reason?: string;
    }) => libraryApi.remove(id, { revokeOwnGroupShare, reason }),
    onSuccess: invalidate,
  });
}

export function useDraftDecision() {
  const invalidate = useInvalidateLibrary();
  return useMutation({
    mutationFn: ({
      id,
      action,
      body,
    }: {
      id: string;
      action: 'approve' | 'reject';
      body: DraftDecisionIn;
    }) =>
      action === 'approve'
        ? libraryApi.approveDraft(id, body)
        : libraryApi.rejectDraft(id, body),
    onSuccess: invalidate,
  });
}

export function useRestoreAsset() {
  const invalidate = useInvalidateLibrary();
  return useMutation({ mutationFn: (id: string) => libraryApi.restore(id), onSuccess: invalidate });
}

export function usePurgeAsset() {
  const invalidate = useInvalidateLibrary();
  return useMutation({ mutationFn: (id: string) => libraryApi.purge(id), onSuccess: invalidate });
}

export function useBulkAction() {
  const invalidate = useInvalidateLibrary();
  return useMutation({
    mutationFn: (body: BulkIn) => libraryApi.bulk(body),
    onSettled: invalidate,
  });
}

export function useUploadAsset() {
  const invalidate = useInvalidateLibrary();
  return useMutation({
    mutationFn: ({
      file,
      kind,
      onProgress,
    }: {
      file: File;
      kind: 'image' | 'audio' | 'text';
      onProgress?: (ratio: number) => void;
    }) => libraryApi.upload(file, kind, { onProgress }),
    onSuccess: invalidate,
  });
}

export function useCreatePack() {
  const invalidate = useInvalidateLibrary();
  return useMutation({
    mutationFn: (body: { name: string; assetIds: string[]; tagIds?: string[] }) =>
      libraryApi.createPack(body),
    onSuccess: invalidate,
  });
}

export function useDeletePack() {
  const invalidate = useInvalidateLibrary();
  return useMutation({ mutationFn: (id: string) => libraryApi.deletePack(id), onSuccess: invalidate });
}

export function useCreateTag() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ name, parentId }: { name: string; parentId?: string }) =>
      libraryApi.createTag(name, parentId),
    onSuccess: () => client.invalidateQueries({ queryKey: libraryKeys.tags() }),
  });
}

/** 改名 / 移动 / 删除都要让标签计数与所有打标选择器一起回流 */
function useInvalidateTags() {
  const client = useQueryClient();
  return () => {
    void client.invalidateQueries({ queryKey: libraryKeys.tags() });
    void client.invalidateQueries({ queryKey: libraryKeys.lists() });
    void client.invalidateQueries({ queryKey: libraryKeys.details() });
  };
}

export function useRenameTag() {
  const invalidate = useInvalidateTags();
  return useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) =>
      libraryApi.renameTag(id, name),
    onSuccess: invalidate,
  });
}

export function useMoveTag() {
  const invalidate = useInvalidateTags();
  return useMutation({
    mutationFn: ({ id, parentId }: { id: string; parentId: string | null }) =>
      libraryApi.moveTag(id, parentId),
    onSuccess: invalidate,
  });
}

export function useDeleteTag() {
  const invalidate = useInvalidateTags();
  return useMutation({
    mutationFn: (id: string) => libraryApi.deleteTag(id),
    onSuccess: invalidate,
  });
}

/* ------------------------------------------------------------------ 共享 */

export function useShareRequests(scope: 'mine' | 'pending', enabled = true) {
  return useQuery({
    queryKey: libraryKeys.requests(scope),
    queryFn: () => libraryApi.shareRequests(scope),
    enabled,
    staleTime: 15_000,
  });
}

function useInvalidateRequests() {
  const client = useQueryClient();
  const invalidate = useInvalidateLibrary();
  return () => {
    invalidate();
    void client.invalidateQueries({ queryKey: [...libraryKeys.all, 'requests'] });
  };
}

export function useCreateShareRequest() {
  const invalidate = useInvalidateRequests();
  return useMutation({
    mutationFn: (body: ShareRequestIn) => libraryApi.createShareRequest(body),
    onSuccess: invalidate,
  });
}

export function useDecideShareRequest() {
  const invalidate = useInvalidateRequests();
  return useMutation({
    mutationFn: ({
      id,
      approve,
      body,
    }: {
      id: string;
      approve: boolean;
      body: ShareDecisionIn;
    }) =>
      approve
        ? libraryApi.approveShareRequest(id, body)
        : libraryApi.rejectShareRequest(id, body),
    onSuccess: invalidate,
  });
}

export function useCancelShareRequest() {
  const invalidate = useInvalidateRequests();
  return useMutation({
    mutationFn: (id: string) => libraryApi.cancelShareRequest(id),
    onSuccess: invalidate,
  });
}

/* -------------------------------------------------------------- 批量下载 */

export function useDownloadJobs(enabled = true) {
  return useQuery({
    queryKey: libraryKeys.downloads(),
    queryFn: libraryApi.downloads,
    enabled,
    /* 有任务在跑时轮询，全部落定就停：每秒一次足够，别再拿 refetchInterval 猜 */
    refetchInterval: (query) =>
      (query.state.data ?? []).some((job) => job.status === 'queued' || job.status === 'running')
        ? 1_500
        : false,
  });
}

export function useCreateDownload() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: DownloadIn) => libraryApi.createDownload(body),
    onSuccess: () => client.invalidateQueries({ queryKey: libraryKeys.downloads() }),
  });
}

/**
 * 取打包好的 zip 并触发保存。
 *
 * mock 模式下适配器回的是 `{code,message,data}` 对象而不是二进制，
 * 这里显式拦一道：宁可提示「演示环境不生成压缩包」，也不给用户存下
 * 一个扩展名是 .zip、打开却报损坏的文件。
 */
export function useSaveDownload() {
  return useMutation({
    mutationFn: async ({ id, filename }: { id: string; filename: string }) => {
      const payload = await libraryApi.downloadFile(id);
      if (!(payload instanceof Blob)) {
        throw new ApiError('演示模式不会生成真实压缩包', -1);
      }
      saveBlob(payload, filename);
      return payload.size;
    },
  });
}

/* ------------------------------------------------------------------ 媒体 */

/**
 * 下载单个资产的原件。与打包下载共用同一道「mock 回的不是二进制」判定：
 * 宁可提示，也不写一个打不开的文件到用户磁盘上。
 */
export function useSaveAssetFile() {
  return useMutation({
    mutationFn: async ({ id, filename }: { id: string; filename: string }) => {
      const payload = await libraryApi.file(id);
      if (!(payload instanceof Blob)) {
        throw new ApiError('演示模式没有真实文件字节', -1);
      }
      saveBlob(payload, filename);
      return payload.size;
    },
  });
}

/**
 * 取受保护的原文件并转成 object URL（视频/音频预览用）。
 *
 * 不能直接把 `/assets/{id}/file` 塞进 `<video src>`：那个地址要 Bearer 头，
 * 浏览器发媒体请求时不会带上，结果是 401 而不是画面。
 */
export function useLibraryMedia(assetId: string | null, enabled: boolean) {
  const query = useQuery({
    queryKey: libraryKeys.media(assetId ?? ''),
    enabled: !!assetId && enabled,
    staleTime: 5 * 60_000,
    queryFn: async () => libraryApi.file(assetId ?? ''),
  });

  const [url, setUrl] = useState<string | undefined>();

  useEffect(() => {
    if (!(query.data instanceof Blob)) {
      setUrl(undefined);
      return undefined;
    }
    const objectUrl = URL.createObjectURL(query.data);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [query.data]);

  return { url, ...query };
}

/* -------------------------------------------------------------- 文本资产 */

/** 文本写操作会同时改「列表摘要 / 详情 / 正文 / 版本历史」四处缓存，一起失效 */
function useInvalidateText() {
  const client = useQueryClient();
  const invalidateLibrary = useInvalidateLibrary();
  return (id: string) => {
    invalidateLibrary();
    void client.invalidateQueries({ queryKey: libraryKeys.texts(id) });
    void client.invalidateQueries({ queryKey: libraryKeys.textVersions(id) });
  };
}

/** 读正文与当前内容版本。编辑器每次打开都重新取，保证 expectedVersion 是最新的。 */
export function useReadText(id: string | null, enabled = true) {
  return useQuery({
    queryKey: libraryKeys.texts(id ?? ''),
    queryFn: () => libraryApi.readText(id ?? ''),
    enabled: !!id && enabled,
  });
}

export function useTextVersions(id: string | null, enabled = true) {
  return useQuery({
    queryKey: libraryKeys.textVersions(id ?? ''),
    queryFn: () => libraryApi.listTextVersions(id ?? ''),
    enabled: !!id && enabled,
  });
}

export function useTextVersion(id: string | null, version: number | null, enabled = true) {
  return useQuery({
    queryKey: libraryKeys.textVersion(id ?? '', version ?? 0),
    queryFn: () => libraryApi.readTextVersion(id ?? '', version ?? 0),
    enabled: !!id && version != null && enabled,
  });
}

export function useCreateTextAsset() {
  const invalidate = useInvalidateLibrary();
  return useMutation({
    mutationFn: (body: TextAssetIn) => libraryApi.createTextAsset(body),
    onSuccess: invalidate,
  });
}

export function useUpdateText() {
  const invalidate = useInvalidateText();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: TextAssetUpdateIn }) =>
      libraryApi.updateText(id, body),
    onSuccess: (_data, variables) => invalidate(variables.id),
  });
}

export function useRestoreTextVersion() {
  const invalidate = useInvalidateText();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: TextAssetRestoreIn }) =>
      libraryApi.restoreTextVersion(id, body),
    onSuccess: (_data, variables) => invalidate(variables.id),
  });
}
