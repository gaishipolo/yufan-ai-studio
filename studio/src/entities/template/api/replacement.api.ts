import { api } from '@/shared/api/http';
import type { ReplacementCatalog } from '@/shared/api/schema';
import type { ReplacementInput, ReplacementRun } from '../model/replacement.types';
export const replacementApi = {
  catalog: () => api.get<ReplacementCatalog>('/templates/replacement-catalog'),
  prepare: (payload: ReplacementInput) => api.post<ReplacementRun>('/templates/replacement-runs', payload, {timeout: 120_000}),
  execute: (id: string) => api.post<ReplacementRun>(`/templates/replacement-runs/${id}/execute`),
};
