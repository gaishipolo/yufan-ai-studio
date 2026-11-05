import { useQuery } from '@tanstack/react-query';
import { api } from '@/shared/api/http';
import type { Paginated } from '@/shared/api/types';
import type { VideoTemplate } from '../model/types';

export const templateApi = {
  list: () => api.get<Paginated<VideoTemplate>>('/templates'),
};

export function useTemplates() {
  return useQuery({
    queryKey: ['templates', 'list'],
    queryFn: () => templateApi.list(),
  });
}
