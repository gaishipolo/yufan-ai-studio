import { useMutation, useQuery } from '@tanstack/react-query';
import { api } from '@/shared/api/http';
import { saveBlob } from '@/shared/lib/download';

/**
 * 取受保护媒体的字节：URL 进，Blob 出。
 *
 * 预览与下载是同一个请求、不同的 URL：预览传派生流（`/stream?v=…`）或图片的
 * 原件，下载必须传 `/file` 原件。**传错地址不会报错**，只会让「下载原始文件」
 * 悄悄存下转码后的版本，所以调用点各自写清楚用途，别互相复用 Blob。
 *
 * 实现只留一份：`responseType` 与超时如果写两遍，改一处漏一处是迟早的事。
 */
function fetchBlob(url: string) {
  return api.get<Blob>(url.replace('/api/v1', ''), undefined, {
    responseType: 'blob',
    timeout: 600_000,
  });
}

const mediaKeys = {
  all: ['remote-media'] as const,
  one: (url: string) => [...mediaKeys.all, url] as const,
};

/**
 * 读一份受保护媒体。
 *
 * 同一个地址的字节不会变（预览地址自带画质版本 `v=`），所以给 5 分钟
 * `staleTime`：列表里悬停来悬停去不该每次都重拉几十 MB。
 */
export function useRemoteBlob(url: string | undefined) {
  return useQuery({
    queryKey: mediaKeys.one(url ?? ''),
    enabled: !!url,
    queryFn: () => fetchBlob(url ?? ''),
    gcTime: 10 * 60_000,
    staleTime: 5 * 60_000,
  });
}

/**
 * 下载原件。不复用 `useRemoteBlob`：预览可以失败，下载必须独立成立
 * （预览挂了仍然要能把文件存下来核对）。
 */
export function useRemoteDownload() {
  return useMutation({
    mutationFn: async ({ url, filename }: { url: string; filename: string }) => {
      const blob = await fetchBlob(url);
      saveBlob(blob, filename);
      return blob.size;
    },
  });
}
