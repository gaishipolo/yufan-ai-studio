import { cn } from '@/shared/lib/cn';

/** 比例条里各段的语气。配色语义与趋势图、指标卡完全一致 —— 同一件事在一个页面里只能是同一个颜色。 */
export type UsageTone = 'primary' | 'danger' | 'mute';

export interface StackedSegment {
  key: string;
  value: number;
  tone: UsageTone;
  /** 悬浮提示里的名字 */
  label: string;
}

export interface StackedBarProps {
  segments: StackedSegment[];
  className?: string;
  /** 整条的一句话描述（读屏用） */
  ariaLabel?: string;
}

const TONE_CLASS: Record<UsageTone, string> = {
  primary: 'bg-primary',
  danger: 'bg-danger/70',
  mute: 'bg-line-strong',
};

/**
 * 分段比例条：一条里按比例切分几段（成功 / 失败 / 进行中）。
 *
 * 为什么不用三根并排的进度条：三根要自己对齐起点，一根切出来的比例天然对齐，
 * 而且**总长就是分母** —— 看一眼就知道这一段占全部尝试的多大比例。
 *
 * 段与段之间不留缝：留缝会让人误以为「中间还有一段没显示」。
 */
export function StackedBar({ segments, className, ariaLabel }: StackedBarProps) {
  const total = segments.reduce((acc, segment) => acc + segment.value, 0);

  return (
    <div
      className={cn('flex h-2 w-full overflow-hidden rounded-xs bg-line-soft', className)}
      role="img"
      aria-label={ariaLabel}
    >
      {total > 0 &&
        segments
          .filter((segment) => segment.value > 0)
          .map((segment) => (
            <span
              key={segment.key}
              className={cn('h-full first:rounded-l-full last:rounded-r-full', TONE_CLASS[segment.tone])}
              style={{ width: `${(segment.value / total) * 100}%` }}
              title={`${segment.label} ${segment.value}`}
            />
          ))}
    </div>
  );
}
