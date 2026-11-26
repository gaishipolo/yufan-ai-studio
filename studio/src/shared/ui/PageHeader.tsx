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
    /* 页头是一条**标题带**：大标题 + 一行眉标 + 一根分隔线。
       以前标题是 19px 半粗、和右上角的按钮差不多大，一屏扫过去
       「标题」和「控件」是同一量级的东西 —— 页面没有主次。
       现在 26px 标题单独占一行，元信息降级成眉标贴在下面，
       一根发丝线把「标题区」和「内容区」切开：翻到任何一页，
       第一眼都知道自己在哪、下面是什么。 */
    <div className={cn('flex flex-col gap-3', className)}>
      <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
        <div className="min-w-0">
          {/* 宋体大标题：全站唯一的衬线字面，其余一律无衬线。 */}
          <h1 className="font-serif text-h1 font-semibold leading-[1.15] tracking-[-.01em] text-ink">
            {title}
          </h1>
          {meta && (
            /* 眉标走等宽 + 宽字距，像报纸上的栏目名与日期行。 */
            <div className="mt-1.5 flex flex-wrap items-center gap-x-2 font-mono text-micro tracking-[.1em] text-ink-mute">
              {meta}
            </div>
          )}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-1.5 md:justify-end">{actions}</div>}
      </div>

      {/* 报纸栏目线：2px 墨线，把「标题区」和「内容区」切开。
          界线是印上去的，不是描出来的 —— 这是纸感的签名动作；
          它从左往右「落笔」画出来，一次 320ms，不喧宾夺主。 */}
      <div className="ink-rule h-[2px] w-full bg-primary" />
    </div>
  );
}
