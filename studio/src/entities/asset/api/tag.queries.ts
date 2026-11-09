import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSessionStore } from '@/shared/access/session';
import type { AssetViewCreate } from '@/shared/api/schema';
import { archiveKeys } from './archive.queries';
import { tagApi } from './tag.api';

/**
 * 标签与保存视图的查询层。
 *
 * 词表的缓存时间比素材长得多：它是受控词表，一个词的增删是管理员动作，
 * 而不是每次进页面都可能变的东西。用 5 分钟 staleTime 换「下拉框不会在
 * 输入过程中换一批词」——那种跳动比多一次请求难查得多。
 */
export const tagKeys = {
  catalog: () => ['asset-tags'] as const,
  views: (owner: string) => ['asset-views', owner] as const,
  /** 单条素材的标签。按 owner 隔离，与 `archiveKeys` 同一种隔离口径。 */
  assetTags: (owner: string, assetId: string) => ['asset-tags-of', owner, assetId] as const,
};

export function useTagCatalog() {
  return useQuery({
    queryKey: tagKeys.catalog(),
    queryFn: tagApi.catalog,
    staleTime: 5 * 60_000,
    // 词表读不到不该让整页打不开：筛选条退化成"只有分类"仍然可用
    retry: 1,
  });
}

export function useAssetViews() {
  const owner = useSessionStore((state) => state.subject.id);
  return useQuery({
    queryKey: tagKeys.views(owner),
    queryFn: async () => (await tagApi.listViews()).items ?? [],
  });
}

export function useSaveAssetView() {
  const client = useQueryClient();
  const owner = useSessionStore((state) => state.subject.id);
  return useMutation({
    mutationFn: (payload: AssetViewCreate) => tagApi.saveView(payload),
    onSuccess: () => client.invalidateQueries({queryKey: tagKeys.views(owner)}),
  });
}

export function useDeleteAssetView() {
  const client = useQueryClient();
  const owner = useSessionStore((state) => state.subject.id);
  return useMutation({
    mutationFn: (id: string) => tagApi.deleteView(id),
    onSuccess: () => client.invalidateQueries({queryKey: tagKeys.views(owner)}),
  });
}

/**
 * 读一条素材的标签。只在浮层打开时发（`enabled` 由调用方给 `assetId`），
 * 列表页不会为了角标把 200 条素材的标签都拉一遍。
 */
export function useAssetTags(assetId: string | null) {
  const owner = useSessionStore((state) => state.subject.id);
  return useQuery({
    queryKey: tagKeys.assetTags(owner, assetId ?? ''),
    queryFn: () => tagApi.assetTags(assetId ?? ''),
    enabled: !!assetId,
    staleTime: 60_000,
  });
}

/**
 * 给一条素材整体替换标签。
 *
 * 失效的是 `archiveKeys.all(owner)`：标签挂在素材上，列表里每条都可能变；
 * 只失效「当前这条」需要一个按 id 的 key，而列表卡片并不单独缓存，
 * 那个粒度在这里是多余的。
 */
export function useSetAssetTags() {
  const client = useQueryClient();
  const owner = useSessionStore((state) => state.subject.id);
  return useMutation({
    mutationFn: ({assetId, tagIds}: {assetId: string; tagIds: string[]}) =>
      tagApi.setAssetTags(assetId, tagIds),
    onSuccess: (_data, variables) => {
      void client.invalidateQueries({queryKey: archiveKeys.all(owner)});
      // 详情浮层读的是单独一条：不失效就会在保存后继续显示旧标签
      void client.invalidateQueries({queryKey: tagKeys.assetTags(owner, variables.assetId)});
      // 词表的 `useCount` 会变：不失效的话筛选项的计数会停在旧值上
      void client.invalidateQueries({queryKey: tagKeys.catalog()});
    },
  });
}
