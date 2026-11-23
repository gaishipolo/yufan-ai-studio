import { QueryClient } from '@tanstack/react-query';
import { toast } from '@/shared/lib/toast';
import { shouldRetryQuery } from './retry';
import { isStateChanged } from './types';

/**
 * 并发冲突的统一处置。
 *
 * 后端把「你读到的那份状态已经过期」的 409 统一标成
 * `details.reason = 'state_changed'`（见 `core/exceptions.py::StateChanged`），
 * 与「重名 / 已结束 / 不是你的」那类**业务冲突**分开。两者的处置正好相反：
 *
 * - 业务冲突：重试没用，得换个做法；
 * - 状态过期：要**重读权威状态**，让用户确认后重做。
 *
 * 这一层以前是空的：`isStateChanged` 定义了却零调用，页面各自 toast 一句
 * 「请刷新后重试」，而数据还停在旧快照上——用户看到的仍是别人改之前的样子，
 * 于是只能凭猜再点一次（那一次又会撞上同一个 409）。
 */
function handleStateChanged(error: unknown): void {
  if (!isStateChanged(error)) return;

  // 全量失效：一次状态过期意味着服务端那边已经变了，界面上任何一处旧快照
  // 都可能是错的。只失效局部容易漏——漏一处就留一处「看起来没变」的数据，
  // 用户照着它做决定，结果还是错的。
  void queryClient.invalidateQueries();
  toast('数据已被其他人修改，已为你刷新，请确认后重试', 'warning');
}

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
      onError: handleStateChanged,
    },
  },
});
