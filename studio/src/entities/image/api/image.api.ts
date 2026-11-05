import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/shared/api/http';
import type { PageParams, Paginated } from '@/shared/api/types';
import type { GenerateImagePayload, ImageJob } from '../model/types';

export const imageKeys = {
  all: ['images'] as const,
  list: (page: number) => [...imageKeys.all, 'list', page] as const,
};

export const imageApi = {
  /** `GET /images` 已分页（后端默认 24 条/页）：不传 page 只有第一页，total 却是全量 */
  list: (params: PageParams = {}) => api.get<Paginated<ImageJob>>('/images', { ...params }),
  generate: (payload: GenerateImagePayload) => api.post<ImageJob>('/generations/image', payload),
};

export function useImages(page = 1, pageSize?: number) {
  return useQuery({
    // 页码进 key：翻页是不同的一屏数据，共用缓存会先闪上一页
    queryKey: imageKeys.list(page),
    queryFn: () => imageApi.list({ page, pageSize }),
  });
}

export function useGenerateImage() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: GenerateImagePayload) => imageApi.generate(payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: imageKeys.all });
    },
  });
}
