import { api } from '@/shared/api/http';
import type { PackDraftIn, PackManaged, PackManagedList, PackPublicList } from '@/shared/api/schema';

export const packApi = {
  list: () => api.get<PackManagedList>('/templates/replacement-packs'),
  available: (params: {kind: string; keyword: string; page: number}) => api.get<PackPublicList>('/templates/replacement-packs/available', {...params, pageSize: 24}),
  save: (payload: PackDraftIn) => api.put<PackManaged>('/templates/replacement-packs/draft', payload),
  action: ({id, action, expectedRevision}: {id: string; action: 'publish' | 'disable'; expectedRevision: number}) =>
    api.post<PackManaged>(`/templates/replacement-packs/${id}/${action}`, {expectedRevision}),
};
