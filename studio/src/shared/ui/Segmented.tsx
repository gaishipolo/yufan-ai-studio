import { useLayoutEffect, useRef, useState } from 'react';

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
 * 做法**（一个开关有三种模式）。选中态沿用仓库里既有的「选中」语言 ——
 * `bg-primary-soft` + `ring-primary-line` + `text-primary-deep`（与 `TagFilter`、
 * `LibraryFlyout` 同一套），而不是实心蓝底：实心蓝是「这里有个主按钮」的意思。
 * 用 `text-primary-deep` 而不是 `text-primary`：后者在 12px 粗体下对比度约
 * 3.9:1，不到 4.5；深一档是 4.6:1。
 *
 * ## 为什么要有那个会滑的块
 *
 * 以前是「每格自己变色」：白底压在 `#f7fbfc` 的槽上，两者差得只剩一点投影，
 * 三格又都是 12px 的字 —— 切过去几乎看不出发生了什么，用户**不知道自己的
 * 点击生效了没有**。只加深描边也好不了多少：那仍然是「两个静止状态之间的
 * 跳变」，没有方向。
 *
 * 现在是一块淡蓝滑块在槽里**平移**过去：位移本身就在说「从这一档换到了
 * 那一档」。位移用 200ms（`ease-smooth`）——150ms 看不出在动，300ms 显得拖沓。
 * 系统开了「减少动态效果」时只保留落位、不做过渡。
 *
 * 位置是从按钮**实测**来的（`offsetLeft/offsetWidth`），不按 `1/N` 均分算：
 * 「全部 (27)」和「否」这种长短不一的选项在 auto 宽度下并不等宽，按比例算的
 * 滑块会压在两个按钮的接缝上。
 *
 * 解释文字归**调用点**管，不写在组件里：能靠布局表达的（切到「均衡」时可选
 * 槽位直接从右栏消失）就别写；差别在**结果**上、布局表达不了的（模板库那三档：
 * 人物变不变、台词变不变），才由调用点在控件下面挂一行随档位变化的小字。
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
  const trackRef = useRef<HTMLDivElement>(null);
  const [thumb, setThumb] = useState<{ left: number; width: number } | null>(null);
  const activeIndex = options.findIndex(option => option.value === value);

  useLayoutEffect(() => {
    const measure = () => {
      const track = trackRef.current;
      if (!track) return;
      const buttons = track.querySelectorAll<HTMLElement>('[data-segment]');
      const target = activeIndex >= 0 ? buttons[activeIndex] : undefined;
      setThumb(target ? { left: target.offsetLeft, width: target.offsetWidth } : null);
    };
    measure();
    // 窗口缩放、侧栏开合都会改变每格宽度；不重测的话滑块会停在旧位置上。
    window.addEventListener('resize', measure);
    return () => window.removeEventListener('resize', measure);
  }, [activeIndex, options.length, value]);

  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      className={cn(
        'rounded-lg bg-panel-soft p-0.5 ring-1 ring-inset ring-line-soft',
        className,
      )}
    >
      <div ref={trackRef} className="relative flex">
        {/* 实测到之前不渲染：插进来时带着最终位置，就不会有一进页面从最左边
            滑过去的假动作。存在时位置一变就有过渡，正是切换那一下。 */}
        {thumb ? (
          <span
            aria-hidden="true"
            className="pointer-events-none absolute top-0 bottom-0 left-0 rounded-[6px] bg-primary-soft shadow-[0_1px_3px_rgba(16,42,55,.10)] ring-1 ring-inset ring-primary-line transition-transform duration-200 ease-smooth motion-reduce:transition-none"
            style={{transform: `translateX(${thumb.left}px)`, width: thumb.width}}
          />
        ) : null}
        {options.map(option => {
          const active = option.value === value;
          const hint = hints?.[option.value];
          const disabled = disabledValues?.includes(option.value) ?? false;
          return (
            <button
              key={option.value}
              type="button"
              role="radio"
              data-segment=""
              aria-checked={active}
              aria-disabled={disabled || undefined}
              disabled={disabled}
              title={hint}
              onClick={() => onChange(option.value)}
              className={cn(
                // `relative` 是为了让文字压在滑块**上面**：绝对定位的兄弟默认
                // 画在流内内容之上，不加这一句字会被那块滑块盖住。
                'relative flex-1 rounded-[6px] px-2 py-1 text-xs transition-colors duration-150',
                active ? 'font-semibold text-primary-deep' : 'text-ink-mute hover:text-ink-soft',
                disabled && 'cursor-not-allowed opacity-45 hover:text-ink-mute',
              )}
            >
              {option.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
