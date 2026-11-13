import { api } from '@/shared/api/http';
import type {
  TemplatePackCandidateListOut,
  TemplatePackDraftIn,
  TemplatePackListOut,
  TemplatePackOut,
  TemplatePackPromptIn,
  TemplatePackVerifyIn,
  TemplatePackVerifyOut,
} from '@/shared/api/schema';

// 路径写字面量：契约守卫会把它们与后端 EndpointData 逐条比对。
export const templatePackApi = {
  list: (params?: {ownerId?: string; status?: string}) =>
    api.get<TemplatePackListOut>('/template-packs', {params}),
  get: (id: string) => api.get<TemplatePackOut>(`/template-packs/${id}`),
  save: (payload: TemplatePackDraftIn) => api.post<TemplatePackOut>('/template-packs', payload),
  publish: (id: string) => api.post<TemplatePackOut>(`/template-packs/${id}/publish`, {}),
  disable: (id: string) => api.post<TemplatePackOut>(`/template-packs/${id}/disable`, {}),
  updatePrompt: (id: string, payload: TemplatePackPromptIn) =>
    api.patch<TemplatePackOut>(`/template-packs/${id}/prompt`, payload),
  candidates: (id: string, token: string, limit = 100) =>
    api.get<TemplatePackCandidateListOut>(`/template-packs/${id}/candidates`, {
      params: {token, limit},
    }),
  verify: (id: string, payload: TemplatePackVerifyIn) =>
    api.post<TemplatePackVerifyOut>(`/template-packs/${id}/verify`, payload),
};
