import { api } from '@/shared/api/http';
import type {
  ManifestDraftIn,
  ManifestListOut,
  ManifestOut,
  ManifestPublishOut,
  ManifestValidateOut,
} from '@/shared/api/schema';

/**
 * 封装配置（manifest）的接口。
 *
 * 与 `pack.api.ts` 同一约定：路径写成字面量，接口契约守卫才能逐条比对。
 * 唯一的封装入口就是这一组 —— 离线打包器已于 2026-11-14 下线。
 */
export const manifestApi = {
  list: () => api.get<ManifestListOut>('/template-manifests'),
  get: (key: string) => api.get<ManifestOut>(`/template-manifests/${key}`),
  validate: (payload: ManifestDraftIn) =>
    api.post<ManifestValidateOut>('/template-manifests/validate', payload),
  save: (payload: ManifestDraftIn) => api.post<ManifestOut>('/template-manifests', payload),
  publish: (key: string) => api.post<ManifestPublishOut>(`/template-manifests/${key}/publish`),
  disable: (key: string) => api.post<ManifestOut>(`/template-manifests/${key}/disable`),
};
