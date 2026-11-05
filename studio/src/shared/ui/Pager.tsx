import { useEffect } from 'react';
import { cn } from '@/shared/lib/cn';
import { Button } from './Button';

export interface PagerProps {
  /** 当前页，从 1 开始 */
  page: number;
  totalPages: number;
  /** 正在取下一页：按钮临时禁用，避免连点把页码推过界 */
  busy?: boolean;
  onChange: (page: number) => void;
  className?: string;
}

/**
 * 分页条 —— 列表页的公共件。
 *
 * 存在的理由不是"少写几行"，而是修一类具体的事故：
 * 后端已经分页（`/images` 默认 24 条），界面却把 `total`（count(*)）当标题、
 * 只渲染当前页那几条，超过一页的记录在界面上**不存在**，
 * 而标题写着"共 60 张"——同一屏里两个数互相矛盾，用户会当成丢数据报回来。
 *
 * 只有一页时返回 null：没有翻页需求时不该占一行高度。
 * `总条数` 不在这里显示，由调用方放在自己的标题/工具栏上（各页措辞不同）。
 */
export function Pager({ page, totalPages, busy, onChange, className }: PagerProps) {
  const safeTotal = Math.max(1, totalPages);
  const current = Math.min(Math.max(page, 1), safeTotal);

  /* 越界回收：删掉最后一页的最后一条后，`page` 还停在旧页码，
     调用方会继续请求一个不存在的页（后端回 `items: []`），
     界面却显示「第 2 / 2 页」+「没有匹配的数据」—— 看着像数据丢了。
     只把显示归一化是不够的，必须回写一次，让调用方按新页码重新取数。
  effect 放在提前 return 之前：hooks 不能有条件。
     `totalPages <= 0` 表示「还不知道总数」（加载中 / 请求失败），这时不回收 ——
     否则一次网络抖动就会把用户从第 3 页悄悄踢回第 1 页。 */
  useEffect(() => {
    if (totalPages <= 0) return;
    if (page !== current) onChange(current);
  }, [page, current, totalPages, onChange]);

  if (safeTotal <= 1) return null;

  return (
    <div
      className={cn(
        'flex items-center justify-between gap-2 border-t border-line-soft px-3.5 py-2 text-[11.5px] text-ink-mute',
        className,
      )}
    >
      <span>
        第 {current} / {safeTotal} 页
      </span>
      <div className="flex items-center gap-1.5">
        <Button size="sm" disabled={current <= 1 || busy} onClick={() => onChange(current - 1)}>
          上一页
        </Button>
        <Button
          size="sm"
          disabled={current >= safeTotal || busy}
          onClick={() => onChange(current + 1)}
        >
          下一页
        </Button>
      </div>
    </div>
  );
}
