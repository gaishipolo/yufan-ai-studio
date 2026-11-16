import type { ReactNode } from 'react';
import { cn } from '@/shared/lib/cn';

export interface PageHeaderProps {
  title: string;
  /**
   * 标题右边的**实时状态**：数量、筛选摘要、当前视图。
   *
   * 为什么没有 `description`：
   * 以前每个页面开头都有一段话解释「这个页面是干什么的」——
   * 「所有生成任务都在生成工作台或模板库发起，这里只负责查看与整理。」
   * 可页面里明明就摆着筛选条、页签和任务卡，那句话把用户看得见的东西
   * 又念了一遍，还占掉一行。**说明应该是布局的注解，不是版面的常客。**
   *
   * 想交代页面的范围？把它变成能点、能数的状态：真的筛出 12 条就写 12，
   * 别写「这里只负责查看与整理」。
   */
  meta?: ReactNode;
  actions?: ReactNode;
  className?: string;
}

export function PageHeader({ title, meta, actions, className }: PageHeaderProps) {
  return (
    <div className={cn('flex flex-col gap-3 md:flex-row md:items-center md:justify-between', className)}>
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="text-title font-bold tracking-[.2px] text-ink">{title}</h1>
        {meta && <div className="text-meta text-ink-mute">{meta}</div>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-1.5 md:justify-end">{actions}</div>}
    </div>
  );
}
