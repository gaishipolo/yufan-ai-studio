import { useMemo } from 'react';
import { X } from 'lucide-react';
import { cn } from '@/shared/lib/cn';
import type { LibraryTagStatOut } from '@/shared/api/schema';

export interface LibraryTagBarProps {
  /** 后端已按添加时间排好序，这里再排一次只是防止 mock / 旧缓存混进乱序数据 */
  tags: LibraryTagStatOut[];
  selected: string[];
  busy?: boolean;
  onToggle: (tagId: string) => void;
  onClear: () => void;
}

const PILL =
  'inline-flex items-center gap-1 rounded-full border px-2 py-[3px] text-[11px] leading-4 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40';
const IDLE =
  'border-line-soft bg-panel-soft text-ink-soft hover:border-primary-line hover:bg-primary-soft/60 hover:text-primary-deep';
const ACTIVE = 'border-primary bg-primary text-white shadow-sm';

/**
 * 标签筛选条：把整个词表摊开，一个都不藏。
 *
 * 原来是一个多选下拉框：不点开就看不到有哪些标签，也看不到每个标签下面有
 * 多少素材，筛选像是「先猜一个名字」。现在每个标签带当前入口的素材数，
 * 按添加时间先后排列，点一下就筛（可多选，再点一下取消），「全部」清空。
 *
 * 计数来自 `GET /library/tags?state=...&library=...`，与列表同一套可见口径，
 * 且不受当前已选标签影响——否则每点一次，其它标签的数字都会跳一遍。
 */
export function LibraryTagBar({ tags, selected, busy, onToggle, onClear }: LibraryTagBarProps) {
  const ordered = useMemo(
    () =>
      [...tags].sort(
        (a, b) =>
          (a.createdAt ?? '').localeCompare(b.createdAt ?? '') || a.id.localeCompare(b.id),
      ),
    [tags],
  );
  const active = useMemo(() => new Set(selected), [selected]);

  return (
    <div
      className="flex min-h-[28px] flex-wrap items-center gap-1.5"
      role="group"
      aria-label="标签筛选"
    >
      <span className="mr-0.5 shrink-0 text-[11px] font-medium text-ink-mute">标签</span>

      <button
        type="button"
        onClick={onClear}
        aria-pressed={active.size === 0}
        className={cn(PILL, active.size === 0 ? ACTIVE : IDLE)}
      >
        全部
      </button>

      {ordered.map((tag) => {
        const count = tag.assetCount ?? 0;
        const isActive = active.has(tag.id);
        return (
          <button
            key={tag.id}
            type="button"
            onClick={() => onToggle(tag.id)}
            aria-pressed={isActive}
            title={`筛选「${tag.name}」· 当前入口 ${count} 条`}
            className={cn(PILL, isActive ? ACTIVE : IDLE, count === 0 && !isActive && 'opacity-60')}
          >
            <span className="max-w-[110px] truncate">{tag.name}</span>
            <span
              className={cn(
                'min-w-[17px] rounded-full px-1 text-center text-[10px] tabular-nums',
                isActive ? 'bg-white/25 text-white' : 'bg-panel text-ink-mute',
              )}
            >
              {count}
            </span>
          </button>
        );
      })}

      {!ordered.length && (
        <span className="text-[11px] text-ink-mute">
          {busy ? '标签加载中…' : '还没有标签，给素材打标后会出现在这里'}
        </span>
      )}

      {active.size > 0 && (
        <button
          type="button"
          onClick={onClear}
          className="ml-1 inline-flex items-center gap-0.5 text-[11px] text-ink-mute hover:text-primary-deep"
        >
          <X size={11} /> 清除筛选
        </button>
      )}

      {ordered.length > 0 && (
        <span className="ml-auto shrink-0 text-[10.5px] text-ink-mute/80">按添加时间 · 可多选</span>
      )}
    </div>
  );
}
