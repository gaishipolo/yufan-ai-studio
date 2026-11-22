/**
 * 用量看板的展示格式化。
 *
 * ## 为什么一律按 UTC 显示
 *
 * 后端的时间桶边界是 UTC（`core.ports.usage_metrics` 的口径）。如果界面上按
 * 本地时区显示，一条 UTC 23:00 的记录会显示成「第二天 07:00」—— 而它在折线图
 * 上属于前一天。用户会看到「图表在这一天有条记录，列表里却是另一天」。
 *
 * 所以：图表、列表、区间全是 UTC，一套时区看到底。
 */

/** 秒 → 「1 小时 23 分」/「12 分 30 秒」/「45 秒」。 */
export function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  if (total < 60) return `${total} 秒`;
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  if (minutes < 60) return rest ? `${minutes} 分 ${rest} 秒` : `${minutes} 分`;
  const hours = Math.floor(minutes / 60);
  const restMinutes = minutes % 60;
  return restMinutes ? `${hours} 小时 ${restMinutes} 分` : `${hours} 小时`;
}

/** 千分位。 */
export function formatNumber(value: number): string {
  return value.toLocaleString('zh-CN');
}

/** ISO 时间 → 「2026-11-22 14:30」（UTC）。 */
export function formatDateTime(iso: string): string {
  const moment = new Date(iso);
  if (Number.isNaN(moment.getTime())) return iso;
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${moment.getUTCFullYear()}-${pad(moment.getUTCMonth() + 1)}-${pad(
    moment.getUTCDate(),
  )} ${pad(moment.getUTCHours())}:${pad(moment.getUTCMinutes())}`;
}

/** 百分比（0-1 → 「12.3%」）。 */
export function formatPercent(ratio: number): string {
  return `${(ratio * 100).toFixed(1)}%`;
}
