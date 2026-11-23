import type { UsageGranularity, UsageScope } from '@/entities/usage/model/types';

export type RangeKey = '7d' | '30d' | '90d';

export const RANGE_OPTIONS: { value: RangeKey; label: string; days: number }[] = [
  { value: '7d', label: '近 7 天', days: 7 },
  { value: '30d', label: '近 30 天', days: 30 },
  { value: '90d', label: '近 90 天', days: 90 },
];

export const GRANULARITY_OPTIONS: { value: UsageGranularity; label: string }[] = [
  { value: 'day', label: '按日' },
  { value: 'week', label: '按周' },
];

export const SCOPE_OPTIONS: { value: UsageScope; label: string }[] = [
  { value: 'mine', label: '我的' },
  { value: 'all', label: '全部成员' },
];

/** 按 UTC 取日期字符串，和后端的桶边界保持一致。 */
function isoDate(moment: Date): string {
  return moment.toISOString().slice(0, 10);
}

/**
 * 「近 N 天」的区间。
 *
 * 含首尾：近 7 天 = 今天往前 6 天到今天，一共 7 个桶。
 */
export function rangeBounds(key: RangeKey): { start: string; end: string } {
  const days = RANGE_OPTIONS.find((option) => option.value === key)?.days ?? 30;
  const end = new Date();
  const start = new Date(end.getTime() - (days - 1) * 86_400_000);
  return { start: isoDate(start), end: isoDate(end) };
}
