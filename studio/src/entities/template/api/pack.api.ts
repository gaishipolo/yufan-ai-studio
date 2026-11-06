import { api } from '@/shared/api/http';
import type { PackDraftIn, PackManaged, PackManagedList, PackPublicList } from '@/shared/api/schema';

// 同 archive.api：动作写成字面量路径，才能被接口契约守卫逐条比对。
const ACTION_PATH: Record<'publish' | 'disable', (id: string) => string> = {
  publish: (id) => `/templates/replacement-packs/${id}/publish`,
  disable: (id) => `/templates/replacement-packs/${id}/disable`,
};

export const packApi = {
  list: () => api.get<PackManagedList>('/templates/replacement-packs'),
  available: (params: {kind: string; keyword: string; page: number}) => api.get<PackPublicList>('/templates/replacement-packs/available', {...params, pageSize: 24}),
  save: (payload: PackDraftIn) => api.put<PackManaged>('/templates/replacement-packs/draft', payload),
  action: ({id, action, expectedRevision}: {id: string; action: 'publish' | 'disable'; expectedRevision: number}) =>
    api.post<PackManaged>(ACTION_PATH[action](id), {expectedRevision}),
};
