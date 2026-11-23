import type { UsageBreakdownItemOut } from '../model/types';
import { cn } from '@/shared/lib/cn';

export interface BreakdownBarsProps {
  items: UsageBreakdownItemOut[];
  /** 每行右侧显示的量纲名（次 / 张 / 秒） */
  metricLabel?: string;
  className?: string;
}

/**
 * 构成条（横向）。
 *
 * 按占比画长度：占比是后端算好的，前端不自己再除一遍 —— 两处都除的话，
 * 将来口径一改，两边就会差一点点，而用户看到的正是那一点点。
 *
 * 一行三段：**名字 / 条 / 数字**。条给相对感，数字给精确值，两者缺一不可 ——
 * 只有条看不出「12% 是 3 次还是 300 次」。
 *
 * 行的悬停反馈用背景而不是变色：变色会让读者怀疑「数字是不是变了」。
 */
export function BreakdownBars({ items, metricLabel = '次', className }: BreakdownBarsProps) {
  if (items.length === 0) {
    return <p className="py-6 text-center text-meta text-ink-mute">该区间没有数据</p>;
  }
  return (
    <ul className={cn('space-y-0.5', className)}>
      {items.map((item) => (
        <li
          key={`${item.dimension}-${item.key}`}
          className="group -mx-1.5 grid grid-cols-[minmax(0,7rem)_minmax(0,1fr)_auto] items-center gap-3 rounded-lg px-1.5 py-1.5 transition-colors hover:bg-panel-soft sm:grid-cols-[minmax(0,11rem)_minmax(0,2fr)_auto]"
        >
          <span className="truncate text-body text-ink" title={item.label}>
            {item.label}
          </span>
          <span className="h-2 overflow-hidden rounded-full bg-line-soft">
            <span
              className="block h-full rounded-full bg-primary transition-[width,background-color] duration-300 ease-smooth group-hover:bg-primary-deep"
              style={{ width: `${Math.max(item.share * 100, item.total > 0 ? 1.5 : 0)}%` }}
            />
          </span>
          <span className="flex items-baseline gap-1.5 whitespace-nowrap font-mono text-meta tabular-nums">
            <span className="text-ink-soft transition-colors group-hover:text-ink">{item.total}</span>
            <span className="text-ink-mute">{metricLabel}</span>
            {item.share > 0 && <span className="text-ink-mute">{Math.round(item.share * 100)}%</span>}
          </span>
        </li>
      ))}
    </ul>
  );
}
