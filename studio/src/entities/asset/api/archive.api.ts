import { api } from '@/shared/api/http';
import type { ArchivedAsset, ArchiveList } from '@/shared/api/schema';
import type { ArchiveCategory } from '../model/archive.types';
export const archiveApi = {
  list: (category: ArchiveCategory | 'all') => api.get<ArchiveList>('/assets/archive', {category}),
  upload: ({file, category}: {file: File; category: ArchiveCategory}) =>
    api.post<ArchivedAsset>('/assets/archive/upload', file, {params: {filename: file.name, category},
      headers: {'Content-Type': 'application/octet-stream'}, timeout: 600_000}),
  import: ({relativePath, category}: {relativePath: string; category: ArchiveCategory}) =>
    api.post<ArchivedAsset>('/assets/archive/import', {relativePath, category}, {timeout: 120_000}),
  media: (url: string) => api.get<Blob>(url.replace('/api/v1', ''), undefined,
    {responseType: 'blob', timeout: 600_000}),
};
