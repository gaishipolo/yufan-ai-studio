/**
 * 用量看板的类型。
 *
 * 全部从 `shared/api/schema.ts` 重导出 —— 那是后端 openapi 生成的单一事实源。
 * 手写一份必然会漂：后端加一个字段，前端这边不会自动知道，只能等有人发现
 * 「接口返回了但界面上看不到」再手工补一次。
 */
export type {
  Page,
  UsageBreakdownItemOut,
  UsageDimension,
  UsageGranularity,
  UsageOverviewOut,
  UsageRecordOut,
  UsageScope,
  UsageSourceOut,
  UsageSummaryOut,
  UsageTrendPointOut,
} from '@/shared/api/schema';

/**
 * 看板的区间选择。
 *
 * `start` / `end` 都是 `YYYY-MM-DD`，**含首尾**。按 UTC 解释 —— 与后端的
 * 桶边界一致，这样「近 7 天」的折线不会因为时区差一天。
 */
export interface UsageRange {
  start?: string;
  end?: string;
}
