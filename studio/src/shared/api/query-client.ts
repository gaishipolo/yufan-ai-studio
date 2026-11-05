import { QueryClient } from '@tanstack/react-query';

/**
 * 全局 Query 客户端
 * 生产环境可在 providers 里接入 onError 上报、重试策略等
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 30_000,
      refetchOnWindowFocus: false,
    },
    mutations: {
      retry: 0,
    },
  },
});
