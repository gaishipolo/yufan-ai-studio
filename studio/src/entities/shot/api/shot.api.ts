import { useQuery } from '@tanstack/react-query';
import { api } from '@/shared/api/http';
import type { Paginated } from '@/shared/api/types';
import type { ShotPreset } from '../model/types';

export const shotApi = {
  list: () => api.get<Paginated<ShotPreset>>('/shots'),
};

export function useShots() {
  return useQuery({
    queryKey: ['shots', 'list'],
    queryFn: () => shotApi.list(),
  });
}
