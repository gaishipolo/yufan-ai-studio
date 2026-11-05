import { QueryClient } from '@tanstack/react-query';
import { shouldRetryQuery } from './retry';

/**
 * 全局 Query 客户端
 * 生产环境可在 providers 里接入 onError 上报。
 *
 * `retry` 是一个**判断**而不是次数：只有网络级失败与 5xx 才重试，
 * 4xx 直接失败（401 已被拦截器清场，再重试只是多发一次必然失败的请求）。
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: shouldRetryQuery,
      staleTime: 30_000,
      refetchOnWindowFocus: false,
    },
    mutations: {
      retry: 0,
    },
  },
});
