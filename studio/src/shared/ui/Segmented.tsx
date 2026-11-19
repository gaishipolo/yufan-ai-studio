import { cn } from '@/shared/lib/cn';

export interface SegmentedOption<T extends string = string> {
  value: T;
  label: string;
}

export interface SegmentedProps<T extends string = string> {
  options: SegmentedOption<T>[];
  value: T;
  onChange: (value: T) => void;
  /** 每个选项的语义说明，作为 `title` 挂在按钮上（悬浮才看得到）。 */
  hints?: Partial<Record<T, string>>;
  /**
   * 不可选的项。**仍然显示**（用户要知道有这个档），但点不动。
   *
   * 与「干脆不渲染这一项」的区别：不渲染等于这个能力不存在，用户不会去问
   * 「为什么别人有我没有」；显示但禁用才需要一句原因，否则就是一个死按钮。
   */
  disabledValues?: readonly T[];
  className?: string;
  /** 无障碍标签 */
  'aria-label'?: string;
}

/**
 * 分段控件：一排互斥的选项，选中的那一格被按下。
 *
 * 与 `Tabs` 的区别：`Tabs` 是**换内容**（页面级），这个是**改同一件事的
 * 做法**（一个开关有三种模式）。视觉上要更「按下去」而不是「被点亮」，
 * 所以选中态用白底 + 投影，而不是实心蓝底 —— 实心蓝是「这里有个主按钮」的意思。
 *
 * **不写说明性小字**：每一格的意思写进 `title`（悬浮才出现），
 * 不在控件下面挂一行解释文字 —— 三格的差别（谁决定素材）看**布局**就懂：
 * 切到「均衡」时可选槽位直接从右栏消失，这就是解释。
 */
export function Segmented<T extends string = string>({
  options,
  value,
  onChange,
  hints,
  disabledValues,
  className,
  'aria-label': ariaLabel,
}: SegmentedProps<T>) {
  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      className={cn(
        'flex items-center gap-0.5 rounded-lg bg-panel-soft p-0.5 ring-1 ring-inset ring-line-soft',
        className,
      )}
    >
      {options.map(option => {
        const active = option.value === value;
        const hint = hints?.[option.value];
        const disabled = disabledValues?.includes(option.value) ?? false;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={active}
            aria-disabled={disabled || undefined}
            disabled={disabled}
            title={hint}
            onClick={() => onChange(option.value)}
            className={cn(
              'flex-1 rounded-[6px] px-2 py-1 text-xs transition-all duration-150',
              active
                ? 'bg-panel font-semibold text-ink shadow-[0_1px_3px_rgba(16,42,55,.12)]'
                : 'text-ink-mute hover:text-ink-soft',
              disabled && 'cursor-not-allowed opacity-45 hover:text-ink-mute',
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
