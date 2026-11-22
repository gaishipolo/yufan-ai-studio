import { api } from '@/shared/api/http';
import type {
  Page,
  UsageGranularity,
  UsageOverviewOut,
  UsageRecordOut,
  UsageScope,
} from '../model/types';

export interface UsageOverviewParams {
  /** `YYYY-MM-DD`，含首尾 */
  start?: string;
  /** `YYYY-MM-DD`，含首尾 */
  end?: string;
  /** 时间桶粒度 */
  granularity?: UsageGranularity;
  /** 数据范围：mine / all */
  scope?: UsageScope;
}

export interface UsageRecordsParams extends UsageOverviewParams {
  page?: number;
  pageSize?: number;
}

/**
 * 用量看板 API。
 *
 * 只有两个接口：总览（汇总 + 趋势 + 构成）和明细。后端把它们合成一个
 * `/usage/overview` 就是为了少跑一遍聚合、少发一次请求。
 */
export const usageApi = {
  overview: (params: UsageOverviewParams) =>
    api.get<UsageOverviewOut>('/usage/overview', { ...params }),
  records: (params: UsageRecordsParams) =>
    api.get<Page<UsageRecordOut>>('/usage/records', { ...params }),
};
