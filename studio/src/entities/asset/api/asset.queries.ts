import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSessionStore } from '@/shared/access/session';
import { assetApi } from './asset.api';
import type { AssetListParams } from '../model/types';

/**
 * key 里带 owner：素材列表按账号隔离，切账号后不能命中上一个人的缓存。
 * 与 `archive.queries.ts` 同一约定。
 */
export const assetKeys = {
  all: (owner: string) => ['assets', owner] as const,
  list: (owner: string, params: AssetListParams) =>
    [...assetKeys.all(owner), 'list', params] as const,
};

export function useAssets(params: AssetListParams) {
  const owner = useSessionStore((state) => state.subject.id);
  return useQuery({
    queryKey: assetKeys.list(owner, params),
    queryFn: () => assetApi.list(params),
    // 翻页时保留上一页数据，避免整块 grid 掉成骨架屏
    placeholderData: (previous) => previous,
  });
}

export function useUploadAsset() {
  const client = useQueryClient();
  const owner = useSessionStore((state) => state.subject.id);
  return useMutation({
    mutationFn: ({ file, onProgress }: { file: File; onProgress?: (percent: number) => void }) =>
      assetApi.upload(file, onProgress),
    onSuccess: () => client.invalidateQueries({ queryKey: assetKeys.all(owner) }),
  });
}

export function useDeleteAsset() {
  const client = useQueryClient();
  const owner = useSessionStore((state) => state.subject.id);
  return useMutation({
    mutationFn: (id: string) => assetApi.remove(id),
    onSuccess: () => client.invalidateQueries({ queryKey: assetKeys.all(owner) }),
  });
}

/**
 * 取素材字节并转成可在 `<img>` / `<video>` 里直接用的 object URL。
 *
 * 用完必须 revoke：不释放的话每翻一页都会把上一页的 Blob 留在内存里，
 * 几十个大视频就能把标签页拖到卡死。
 *
 * 带 `isError` 出去：预览弹窗要区分「还在读」和「读不到」。
 * 只返回字符串的话两者都是空串，`/file` 404 时会永远停在「正在读取文件…」。
 */
export interface AssetBlob {
  url: string;
  isLoading: boolean;
  isError: boolean;
}

export function useAssetBlob(id: string | undefined, kind: 'file' | 'cover'): AssetBlob {
  const owner = useSessionStore((state) => state.subject.id);
  const { data, isLoading, isError } = useQuery({
    queryKey: [...assetKeys.all(owner), kind, id] as const,
    enabled: !!id,
    queryFn: () => (kind === 'file' ? assetApi.file(id ?? '') : assetApi.cover(id ?? '')),
    staleTime: Infinity,
    gcTime: 60_000,
    // 覆盖层关掉就该释放，不做后台重试：404/403 重试三次只会拖慢预览
    retry: false,
  });

  const [url, setUrl] = useState('');
  useEffect(() => {
    if (!data) {
      setUrl('');
      return undefined;
    }
    const value = URL.createObjectURL(data);
    setUrl(value);
    return () => URL.revokeObjectURL(value);
  }, [data]);

  return { url, isLoading, isError };
}

/** 只要地址的场景（卡片封面）用这个，省得每次都解构。 */
export function useAssetObjectUrl(id: string | undefined, kind: 'file' | 'cover'): string {
  return useAssetBlob(id, kind).url;
}
