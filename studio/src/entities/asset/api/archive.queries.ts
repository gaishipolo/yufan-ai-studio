import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSessionStore } from '@/shared/access/session';
import { archiveApi } from './archive.api';
import type { ArchiveCategory } from '../model/archive.types';
export const archiveKeys = {
  all: (owner: string) => ['archive-assets', owner] as const,
  list: (owner: string, category: ArchiveCategory | 'all') => [...archiveKeys.all(owner), 'list', category] as const,
  media: (owner: string, url: string | undefined) => ['archive-media', owner, url] as const,
};
export function useArchiveAssets(category: ArchiveCategory | 'all' = 'all') {
  const owner = useSessionStore(state => state.subject.id);
  return useQuery({queryKey: archiveKeys.list(owner, category), queryFn: () => archiveApi.list(category)});
}
export function useArchiveUpload() {
  const client = useQueryClient();
  const owner = useSessionStore(state => state.subject.id);
  return useMutation({mutationFn: archiveApi.upload,
    onSuccess: () => client.invalidateQueries({queryKey: archiveKeys.all(owner)})});
}
export function useArchiveImport() {
  const client = useQueryClient();
  const owner = useSessionStore(state => state.subject.id);
  return useMutation({mutationFn: archiveApi.import,
    onSuccess: () => client.invalidateQueries({queryKey: archiveKeys.all(owner)})});
}
export function useArchivedMedia(url: string | undefined) {
  const owner = useSessionStore(state => state.subject.id);
  return useQuery({queryKey: archiveKeys.media(owner, url), enabled: !!url,
    queryFn: () => archiveApi.media(url ?? ''), gcTime: 60_000, staleTime: Infinity});
}
