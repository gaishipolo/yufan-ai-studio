import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSessionStore } from '@/shared/access/session';
import type { AdoptRequest, BatchAdoptRequest } from '@/shared/api/schema';
import { archiveKeys } from './archive.queries';
import { tagKeys } from './tag.queries';
import { candidateApi } from './candidate.api';

/**
 * 候选的查询层。
 *
 * 失效范围是**整棵素材树**：采纳会让一张图从候选变成正式素材（列表、分类角标、
 * 配额都要变），而这三处的 key 都在 `archiveKeys.all` 下。省这一步的代价是
 * 「采纳完切回列表还是 3 条」——用户会以为没成功，然后重复点。
 *
 * 候选自身也要失效：采纳/弃用之后这一批就该从横幅里消失。
 */
export const candidateKeys = {
  all: (owner: string) => ['archive-candidates', owner] as const,
};

export function useCandidates(options?: {enabled?: boolean}) {
  const owner = useSessionStore((state) => state.subject.id);
  return useQuery({
    queryKey: candidateKeys.all(owner),
    queryFn: candidateApi.list,
    enabled: options?.enabled ?? true,
  });
}

/** 采纳/弃用/批量采纳共用的善后逻辑：候选与素材两侧一起失效。 */
function useCandidateInvalidate() {
  const client = useQueryClient();
  const owner = useSessionStore((state) => state.subject.id);
  return () => {
    void client.invalidateQueries({queryKey: candidateKeys.all(owner)});
    void client.invalidateQueries({queryKey: archiveKeys.all(owner)});
    void client.invalidateQueries({queryKey: archiveKeys.mediaAll(owner)});
    // 采纳会贴标签：词表的 useCount 跟着变
    void client.invalidateQueries({queryKey: tagKeys.catalog()});
  };
}

export function useAdoptCandidate() {
  const invalidate = useCandidateInvalidate();
  return useMutation({
    mutationFn: ({assetId, payload}: {assetId: string; payload?: AdoptRequest}) =>
      candidateApi.adopt(assetId, payload ?? {}),
    onSuccess: invalidate,
  });
}

export function useDiscardCandidate() {
  const invalidate = useCandidateInvalidate();
  return useMutation({
    mutationFn: (assetId: string) => candidateApi.discard(assetId),
    onSuccess: invalidate,
  });
}

export function useAdoptCandidates() {
  const invalidate = useCandidateInvalidate();
  return useMutation({
    mutationFn: (payload: BatchAdoptRequest) => candidateApi.adoptMany(payload),
    onSuccess: invalidate,
  });
}
