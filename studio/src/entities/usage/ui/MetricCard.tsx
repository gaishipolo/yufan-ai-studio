import type { ReactNode } from 'react';
import { cn } from '@/shared/lib/cn';
import { RatioBar } from './RatioBar';

export interface MetricCardProps {
  label: string;
  value: ReactNode;
  unit?: string;
  /** 数值下面那行小字：口径说明、分项、异常提示 */
  hint?: ReactNode;
  /** 数值下方的比例条（0-1）。给了就把「这格好不好」也画出来。 */
  progress?: number;
  /**
   * 数值字号。
   *
   * `hero` 用 `display`（26px）—— 页面上只给**一个**结论用这个字号，多了就
   * 没有结论可言了；其余用 `title`（19px）。
   */
  size?: 'normal' | 'hero';
  className?: string;
}

/**
 * 一张指标卡。
 *
 * 数字用等宽字（`font-mono tabular-nums`）—— 这一列要能竖着扫，
 * 比例字体的数字上下跳动，看不出「哪个更大」。
 */
export function MetricCard({
  label,
  value,
  unit,
  hint,
  progress,
  size = 'normal',
  className,
}: MetricCardProps) {
  return (
    <div className={cn('rounded-card border border-line-soft bg-panel p-4', className)}>
      <p className="text-meta text-ink-mute">{label}</p>
      <p className="mt-1.5 flex items-baseline gap-1.5">
        <span
          className={cn(
            'font-mono font-semibold tabular-nums leading-none text-ink',
            size === 'hero' ? 'text-display' : 'text-title',
          )}
        >
          {value}
        </span>
        {unit && <span className="text-body text-ink-mute">{unit}</span>}
      </p>
      {progress !== undefined && <RatioBar value={progress} className="mt-3 sm:max-w-[18rem]" />}
      {hint && <p className="mt-2.5 text-meta leading-relaxed text-ink-mute">{hint}</p>}
    </div>
  );
}
