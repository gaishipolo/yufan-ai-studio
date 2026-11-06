import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSessionStore } from '@/shared/access/session';
import { packApi } from './pack.api';
import { replacementKeys } from './replacement.queries';

/** 缓存键工厂：可用列表与草稿列表共用 `packs/{owner}` 前缀，失效一次覆盖两处。 */
export const packKeys = {
  all: (owner: string) => ['packs', owner] as const,
  available: (owner: string, params: {kind: string; keyword: string; page: number}) =>
    [...packKeys.all(owner), 'available', params] as const,
  managed: (owner: string) => [...packKeys.all(owner), 'managed'] as const,
};

export function useAvailablePacks(kind: string, keyword: string, page: number) {
  const owner = useSessionStore(s => s.subject.id);
  return useQuery({queryKey: packKeys.available(owner, {kind, keyword, page}),
    queryFn: () => packApi.available({kind, keyword, page})});
}
export function useManagedPacks() {
  const owner = useSessionStore(s => s.subject.id);
  return useQuery({queryKey: packKeys.managed(owner), queryFn: packApi.list});
}
export function usePackMutations() {
  const client = useQueryClient();
  const owner = useSessionStore(s => s.subject.id);
  const refresh = () => {
    void client.invalidateQueries({queryKey: packKeys.all(owner)});
    void client.invalidateQueries({queryKey: replacementKeys.catalog(owner)});
  };
  return {save: useMutation({mutationFn: packApi.save, onSettled: refresh}),
    action: useMutation({mutationFn: packApi.action, onSettled: refresh})};
}
