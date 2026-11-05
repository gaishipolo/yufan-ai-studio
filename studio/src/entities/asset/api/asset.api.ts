import { useQuery } from '@tanstack/react-query';
import { api } from '@/shared/api/http';
import type { Asset, AssetListParams } from '../model/types';

export interface AssetListResult {
  items: Asset[];
  total: number;
  counts: Record<'all' | 'image' | 'video' | 'audio', number>;
}

export const assetApi = {
  list: (params: AssetListParams) => api.get<AssetListResult>('/assets', { ...params }),
};

export const assetKeys = {
  all: ['assets'] as const,
  list: (params: AssetListParams) => [...assetKeys.all, 'list', params] as const,
};

export function useAssets(params: AssetListParams) {
  return useQuery({
    queryKey: assetKeys.list(params),
    queryFn: () => assetApi.list(params),
  });
}
