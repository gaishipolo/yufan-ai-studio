import { useEffect } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { libtvApi } from './libtv.api';

/** 缓存键：账号面与任务面独立，失效时互不牵连 */
export const libtvKeys = {
  all: ['libtv'] as const,
  account: () => [...libtvKeys.all, 'account'] as const,
  queue: (ids: string[]) => [...libtvKeys.all, 'queue', ids] as const,
};

const QUEUE_POLL_INTERVAL = 5_000;

/** 后台标签页不轮询：隐藏标签页时 React Query 只在窗口失焦时暂停，这里自己挡一层 */
function pageHidden() {
  return typeof document !== 'undefined' && document.visibilityState === 'hidden';
}

/**
 * 账号额度与 0 积分权益。
 *
 * 60 秒一次就够：额度变化只发生在「有人提交」时，而提交会让任务列表刷新，
 * 顺手失效这条查询即可 —— 没必要为一个静态数字每 5 秒打一次上游。
 */
export function useLibtvAccount(enabled = true) {
  const query = useQuery({
    queryKey: libtvKeys.account(),
    queryFn: libtvApi.account,
    enabled,
    staleTime: 60_000,
    retry: false,
    // 账号面是「锦上添花」：拿不到就整块不显示，不能让任务中心跟着报错
    throwOnError: false,
  });
  // 静默失败在排查时是噩梦（「为什么没有额度条」）。留一行控制台线索，
  // 但不弹 toast —— 这是可选信息，不该打断用户。
  useEffect(() => {
    if (query.error) {
      console.warn('[libtv] 账号接口不可用，额度条不显示：', query.error.message);
    }
  }, [query.error]);
  return query;
}

/**
 * 一批上游任务的排队位次。
 *
 * 入参是**上游 task_id**（从任务的 `externalId` 里拆出来的），为空时不发请求 ——
 * 任务中心一页 48 条，没有在跑的任务时不该每 5 秒打一次上游。
 */
export function useLibtvQueue(taskIds: string[], enabled = true) {
  const ids = [...new Set(taskIds.filter(Boolean))].sort();
  return useQuery({
    queryKey: libtvKeys.queue(ids),
    queryFn: () => libtvApi.progress(ids),
    enabled: enabled && ids.length > 0,
    // 换页/换筛选时先留住上一份，避免徽标闪一下
    placeholderData: keepPreviousData,
    retry: false,
    throwOnError: false,
    refetchInterval: query =>
      pageHidden() || (query.state.data?.items.length ?? 0) === 0 ? false : QUEUE_POLL_INTERVAL,
  });
}
