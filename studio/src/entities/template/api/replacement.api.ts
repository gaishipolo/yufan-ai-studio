import { api } from '@/shared/api/http';
import type { ReplacementCatalog, ReplacementReviewIn } from '@/shared/api/schema';
import type { ReplacementInput, ReplacementRun } from '../model/replacement.types';
export const replacementApi = {
  catalog: () => api.get<ReplacementCatalog>('/templates/replacement-catalog'),
  prepare: (payload: ReplacementInput) => api.post<ReplacementRun>('/templates/replacement-runs', payload, {timeout: 120_000}),
  execute: (id: string, billingMode?: 'free' | 'credits') =>
    api.post<ReplacementRun>(`/templates/replacement-runs/${id}/execute`,
      billingMode ? {billingMode} : undefined),
  remove: (id: string) => api.delete<ReplacementRun>(`/templates/replacement-runs/${id}`),
  restore: (id: string) => api.post<ReplacementRun>(`/templates/replacement-runs/${id}/restore`),
  review: (id: string, payload: ReplacementReviewIn) => api.put<ReplacementRun>(`/templates/replacement-runs/${id}/review`, payload),
};
