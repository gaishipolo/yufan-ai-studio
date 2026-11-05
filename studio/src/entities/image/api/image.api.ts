import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/shared/api/http';
import type { Paginated } from '@/shared/api/types';
import type { GenerateImagePayload, ImageJob } from '../model/types';

export const imageKeys = {
  all: ['images'] as const,
  list: () => [...imageKeys.all, 'list'] as const,
};

export const imageApi = {
  list: () => api.get<Paginated<ImageJob>>('/images'),
  generate: (payload: GenerateImagePayload) => api.post<ImageJob>('/generations/image', payload),
};

export function useImages() {
  return useQuery({ queryKey: imageKeys.list(), queryFn: () => imageApi.list() });
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
