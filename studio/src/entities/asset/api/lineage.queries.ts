import { useMutation, useQuery } from '@tanstack/react-query';
import { useSessionStore } from '@/shared/access/session';
import type { DeriveRequest } from '@/shared/api/schema';
import { lineageApi } from './lineage.api';

/**
 * 血缘与衍生的查询层。
 *
 * **血缘不快取到跨素材复用**：`staleTime` 给 30 秒，够在同一个浮层里反复展开
 * 而不重复打请求，又不至于让"刚衍生出来的子图"迟迟不出现（血缘是会随生成
 * 完成的，缓存太久会显示一张还在排队的图）。
 *
 * `derive` 是 mutation 而不是 query：它会做一次可见性 + 授权判定，
 * 且有独立的限流档；把判定塞进 query 的 `queryFn` 会让失败被重试放大。
 */
export const lineageKeys = {
  all: (owner: string) => ['asset-lineage', owner] as const,
  one: (owner: string, assetId: string) => [...lineageKeys.all(owner), assetId] as const,
};

export function useAssetLineage(assetId: string | null) {
  const owner = useSessionStore((state) => state.subject.id);
  return useQuery({
    queryKey: lineageKeys.one(owner, assetId ?? ''),
    queryFn: () => lineageApi.lineage(assetId ?? ''),
    enabled: !!assetId,
    staleTime: 30_000,
  });
}

export function useDeriveFromAsset() {
  return useMutation({
    mutationFn: ({assetId, mode}: {assetId: string; mode: DeriveRequest['mode']}) =>
      lineageApi.derive(assetId, mode),
  });
}
