import { useEffect, useState } from 'react';
import { Button, Select } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';

const PAGE_SIZE_OPTIONS = [
  { value: '10', label: '10 条/页' },
  { value: '20', label: '20 条/页' },
  { value: '50', label: '50 条/页' },
  { value: '100', label: '100 条/页' },
];

/**
 * 用户表格底部分页（截图形态：共 N 条 / 每页条数 / 页码 / 前往第 N 页）。
 *
 * 没有把这些塞进公共 `Pager`：那个组件刻意约定「总条数由调用方放在工具栏」，
 * 且给所有列表页用；为了一个页面的截图去改公共约定，会让别的页面一起变样。
 * 但越界回收必须带过来 —— 删掉最后一页最后一条后，`page` 还停在旧页码，
 * 界面会显示「第 3 / 2 页」+ 空列表，看起来像数据丢了。
 */
export function UserTableFooter({
  page,
  totalPages,
  total,
  pageSize,
  busy,
  onPage,
  onPageSize,
}: {
  page: number;
  totalPages: number;
  total: number;
  pageSize: number;
  busy?: boolean;
  onPage: (page: number) => void;
  onPageSize: (size: number) => void;
}) {
  const [jump, setJump] = useState('');

  const safeTotal = Math.max(1, totalPages);
  const current = Math.min(Math.max(page, 1), safeTotal);

  useEffect(() => {
    if (totalPages <= 0) return;
    if (page !== current) onPage(current);
  }, [page, current, totalPages, onPage]);

  // 页码窗口：当前页居中，最多 5 个；totalPages 未知时一个都不渲染
  const end = Math.min(safeTotal, Math.max(1, current - 2) + 4);
  const start = Math.max(1, end - 4);
  const pageNumbers = totalPages > 0
    ? Array.from({ length: end - start + 1 }, (_, index) => start + index)
    : [];

  const go = (next: number) => {
    const target = Math.min(Math.max(next, 1), safeTotal);
    onPage(target);
    setJump('');
  };

  return (
    <div className="flex flex-wrap items-center gap-2 border-t border-line-soft px-3.5 py-2 text-[11.5px] text-ink-mute">
      <span>共 {total} 条</span>
      <Select
        options={PAGE_SIZE_OPTIONS}
        value={String(pageSize)}
        aria-label="每页条数"
        className="w-[92px]"
        onChange={(event) => {
          onPageSize(Number(event.target.value));
          onPage(1);
        }}
      />

      <div className="ml-auto flex items-center gap-1">
        <Button size="sm" disabled={current <= 1 || busy} onClick={() => go(current - 1)}>
          上一页
        </Button>
        {pageNumbers.map((number) => (
          <button
            key={number}
            type="button"
            aria-label={`第 ${number} 页`}
            aria-current={number === current ? 'page' : undefined}
            className={cn(
              'grid h-7 min-w-7 place-items-center rounded-lg border px-1.5 text-[11.5px] transition-colors',
              number === current
                ? 'border-primary bg-primary text-white'
                : 'border-line bg-white text-ink-soft hover:border-line-strong',
            )}
            disabled={busy}
            onClick={() => go(number)}
          >
            {number}
          </button>
        ))}
        {totalPages > 0 && end < totalPages && <span className="px-1">…</span>}
        <Button size="sm" disabled={current >= safeTotal || busy} onClick={() => go(current + 1)}>
          下一页
        </Button>
      </div>

      <div className="flex items-center gap-1">
        <span>前往</span>
        <input
          type="number"
          min={1}
          max={safeTotal}
          aria-label="前往页码"
          value={jump}
          className="h-7 w-12 rounded-lg border border-line bg-white px-1.5 text-center text-[11.5px] text-ink outline-none focus:border-primary-line"
          onChange={(event) => setJump(event.target.value)}
          onKeyDown={(event) => {
            if (event.key !== 'Enter') return;
            const parsed = Number(jump);
            if (Number.isFinite(parsed) && parsed > 0) go(Math.floor(parsed));
          }}
        />
        <span>页</span>
      </div>
    </div>
  );
}
