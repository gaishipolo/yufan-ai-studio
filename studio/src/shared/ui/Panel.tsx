import type { ReactNode } from 'react';
import { cn } from '@/shared/lib/cn';

/**
 * 面板：工作台的基本容器 —— 一块白板，边界用一根冷灰细线划出来。
 *
 * 全站之前是「每个页面自己写一遍 `<section className="rounded-card border
 * border-line-soft bg-panel p-3.5">`」，样式漂了没人知道。收进一个基元之后，
 * 面板就是面板：白底、12px 圆角、1px 线、内边距一致。
 */
export interface PanelProps {
  children: ReactNode;
  className?: string;
}

export function Panel({ children, className }: PanelProps) {
  return (
    <section className={cn('rounded-card border border-line-soft bg-panel p-3.5', className)}>
      {children}
    </section>
  );
}

/**
 * 面板标题行。
 *
 * 标题就是标题，靠**字号**站住（lead 15px 半粗），不再靠前面那颗装饰圆点 ——
 * 圆点不编码任何东西，删掉。标题右边的 `meta` 放**数得出来的东西**
 * （几个镜头、几个任务），`actions` 放操作。
 *
 * 以前这里常写成「自动拆解 · 4 个镜头」——「自动拆解」是对机制的解释，
 * 用户看不到机制也用不到机制；4 是数得出来的，留在那儿。
 */
export interface PanelHeaderProps {
  title: ReactNode;
  /** 计数 / 状态，用等宽字：这一列要能竖着扫 */
  meta?: ReactNode;
  actions?: ReactNode;
  className?: string;
  /**
   * 窄屏时把 `actions` 换到标题下面一行。
   *
   * 默认关闭 —— 标题行右侧的操作是「窄一点也放得下」的那种（一两个图标按钮）。
   * 但像分段控件这种**本身就有宽度**的操作，硬塞在标题右边会把标题挤成
   * 「用量构 / 成」两行；让它落到下一行，标题就还是标题。
   */
  stackActions?: boolean;
}

export function PanelHeader({
  title,
  meta,
  actions,
  className,
  stackActions = false,
}: PanelHeaderProps) {
  const heading = (
    <>
      <h2 className="text-lead font-semibold tracking-[.2px] text-ink">{title}</h2>
      {meta && <span className="font-mono text-meta tabular-nums text-ink-mute">{meta}</span>}
    </>
  );

  if (!stackActions) {
    return (
      <header className={cn('mb-2.5 flex items-baseline gap-2', className)}>
        {heading}
        {actions && <div className="ml-auto flex shrink-0 items-center gap-1.5">{actions}</div>}
      </header>
    );
  }

  return (
    <header className={cn('mb-2.5 flex flex-col gap-2 sm:flex-row sm:items-baseline', className)}>
      <div className="flex items-baseline gap-2">{heading}</div>
      {actions && <div className="flex items-center gap-1.5 sm:ml-auto">{actions}</div>}
    </header>
  );
}
