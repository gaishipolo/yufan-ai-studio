import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { saveBlob } from '@/shared/lib/download';
import { useSessionStore } from '@/shared/access/session';
import { archiveApi, type ArchiveSearch } from './archive.api';
import type { ArchiveCategory } from '../model/archive.types';
export const archiveKeys = {
  all: (owner: string) => ['archive-assets', owner] as const,
  list: (owner: string, category: ArchiveCategory | 'all') => [...archiveKeys.all(owner), 'list', category] as const,
  /** 预览流的公共前缀：失效时不用枚举每个 url。 */
  mediaAll: (owner: string) => ['archive-media', owner] as const,
  media: (owner: string, url: string | undefined) => [...archiveKeys.mediaAll(owner), url] as const,
};
/**
 * `enabled` 用于「暂时不需要这页数据」的场景（例如核对批次时才读列表）：
 * 不发请求，也不把查询留在 pending 里。
 */
export function useArchiveAssets(category: ArchiveCategory | 'all' = 'all', options?: ArchiveSearch & {enabled?: boolean}) {
  const owner = useSessionStore(state => state.subject.id);
  const {enabled = true, ...params} = options ?? {};
  return useQuery({queryKey: [...archiveKeys.list(owner, category), params], queryFn: () => archiveApi.list(category, params),
    enabled});
}
export function useArchiveChange() {
  const client = useQueryClient();
  const owner = useSessionStore(state => state.subject.id);
  return useMutation({mutationFn: archiveApi.change, onSettled: () => {
    void client.invalidateQueries({queryKey: archiveKeys.all(owner)});
    void client.invalidateQueries({queryKey: archiveKeys.mediaAll(owner)});
  }});
}
export function useArchiveSelection(id: string | null) {
  const owner = useSessionStore(state => state.subject.id);
  return useQuery({queryKey: [...archiveKeys.all(owner), 'selection', id], enabled: !!id,
    queryFn: () => archiveApi.detail(id ?? '')});
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
  // 预览 URL 自带画质版本（`v=`），同一地址的字节不再变：没必要每次悬停都重拉。
  return useQuery({queryKey: archiveKeys.media(owner, url), enabled: !!url,
    queryFn: () => archiveApi.media(url ?? ''), gcTime: 10 * 60_000, staleTime: 5 * 60_000});
}

/**
 * 下载原件。`url` 必须是 `/file`（或生成结果的原件地址），不能是 `/stream` 派生流。
 * 不复用 `useArchivedMedia`：预览可以失败，下载必须独立成立。
 */
export function useArchiveDownload() {
  return useMutation({
    mutationFn: async ({url, filename}: {url: string; filename: string}) => {
      const blob = await archiveApi.download(url);
      saveBlob(blob, filename);
      return blob.size;
    },
  });
}
