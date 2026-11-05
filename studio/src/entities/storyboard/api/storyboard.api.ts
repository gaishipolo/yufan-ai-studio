import { useQuery } from '@tanstack/react-query';
import { api } from '@/shared/api/http';
import type { Paginated } from '@/shared/api/types';
import type { Storyboard } from '../model/types';

export const storyboardApi = {
  list: () => api.get<Paginated<Storyboard>>('/storyboards'),
};

export function useStoryboards() {
  return useQuery({
    queryKey: ['storyboards', 'list'],
    queryFn: () => storyboardApi.list(),
  });
}
