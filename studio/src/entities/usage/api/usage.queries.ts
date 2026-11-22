import { useQuery } from '@tanstack/react-query';
import { usageApi, type UsageOverviewParams, type UsageRecordsParams } from './usage.api';

export const usageKeys = {
  all: ['usage'] as const,
  overview: (params: UsageOverviewParams) => [...usageKeys.all, 'overview', params] as const,
  records: (params: UsageRecordsParams) => [...usageKeys.all, 'records', params] as const,
};

/**
 * 用量总览。
 *
 * `params` 必须是稳定引用（useMemo 出来的），否则每次渲染都是新 key，
 * react-query 会当成「换了参数」重新拉一次。
 *
 * `enabled`：页面把它放在页签里，没点到的页签不请求 —— 两个接口都发一遍
 * 是白花的带宽（明细一次 20 条）。
 */
export function useUsageOverview(params: UsageOverviewParams, enabled = true) {
  return useQuery({
    queryKey: usageKeys.overview(params),
    queryFn: () => usageApi.overview(params),
    staleTime: 60_000,
    enabled,
  });
}

/** 用量明细。 */
export function useUsageRecords(params: UsageRecordsParams, enabled = true) {
  return useQuery({
    queryKey: usageKeys.records(params),
    queryFn: () => usageApi.records(params),
    staleTime: 60_000,
    enabled,
  });
}
