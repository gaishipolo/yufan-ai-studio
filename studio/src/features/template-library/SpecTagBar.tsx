import { useMemo, useState } from 'react';
import { X } from 'lucide-react';
import { cn } from '@/shared/lib/cn';
import type { TagOut } from '@/shared/api/schema';
import { templateTagDepth, templateTagLabel } from './specTagUtils';

const PILL =
  'inline-flex shrink-0 items-center gap-1 rounded-xs border px-2.5 py-[3px] text-body leading-4 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40';
const IDLE =
  'border-line-soft bg-panel-soft text-ink-soft hover:border-primary-line hover:bg-primary-soft/60 hover:text-primary-deep';
const ACTIVE = 'border-primary bg-primary text-white shadow-sm';
/* 行标题固定 48px + 截断：两个行标题都是「维度 / 取值」，宽一点会让
   第二行的 pills 挤到换行。 */
const ROW_LABEL = 'w-12 shrink-0 truncate text-body font-medium text-ink-mute';

export interface SpecTagBarProps {
  /** `GET /template-specs/tags` 的平铺词表。 */
  tags: TagOut[];
  /** 选中的标签 id（一级维度 / 二级取值都算）—— 由容器持有，不进 URL。 */
  selected: string[];
  busy?: boolean;
  onChange: (next: string[]) => void;
}

/**
 * 模板标签筛选条：**两行**，第一行「维度」（depth 1），第二行「取值」（depth 2）。
 * 第二行按选中的维度收窄（没选维度就列全部取值）。
 *
 * 组合语义贴在行标题的 `title` 里（悬浮才看）：同维度 OR、跨维度 AND ——
 * 常驻版面干净，规则又挂在它约束的那一行上。
 */
export function SpecTagBar({ tags, selected, busy, onChange }: SpecTagBarProps) {
  const [dimensionQuery, setDimensionQuery] = useState('');
  const [valueQuery, setValueQuery] = useState('');

  const dimensions = useMemo(
    () => tags.filter((tag) => templateTagDepth(tag) === 1),
    [tags],
  );
  /* 收窄按**选中的维度**算，不按选中项自身 —— 选了「产品 / 6036」之后还得
     看得见同维度的兄弟取值，否则「同维度多选是或」没法做。 */
  const selectedDimensions = useMemo(
    () => new Set(selected.filter((id) => dimensions.some((tag) => tag.id === id))),
    [selected, dimensions],
  );
  const values = useMemo(() => {
    const allValues = tags.filter((tag) => templateTagDepth(tag) === 2);
    return selectedDimensions.size
      ? allValues.filter((tag) => tag.parentId && selectedDimensions.has(tag.parentId))
      : allValues;
  }, [tags, selectedDimensions]);

  const active = useMemo(() => new Set(selected), [selected]);

  function toggle(tag: TagOut) {
    onChange(
      active.has(tag.id)
        ? selected.filter((id) => id !== tag.id)
        : [...selected, tag.id],
    );
  }

  const visibleDimensions = dimensions.filter((tag) =>
    tag.name.includes(dimensionQuery.trim()),
  );
  const visibleValues = values.filter((tag) =>
    templateTagLabel(tag).includes(valueQuery.trim()),
  );

  return (
    <div className="space-y-1.5" role="group" aria-label="模板标签筛选">
      <div className="flex items-center gap-1.5">
        <span className="text-body font-semibold text-ink">标签筛选</span>
        {selected.length > 0 && (
          <button
            type="button"
            onClick={() => onChange([])}
            className="inline-flex items-center gap-0.5 text-body text-ink-mute hover:text-primary-deep"
          >
            <X size={11} /> 清除筛选
          </button>
        )}
      </div>

      <SpecTagRow
        label="维度"
        labelHint="同一维度内多选是「或」：两个都圈上，两个都要看；与取值行是「且」"
        ariaLabel="模板维度标签"
        allLabel="全部模板维度"
        query={dimensionQuery}
        onQuery={setDimensionQuery}
        searchLabel="搜索模板维度标签"
        allActive={selected.length === 0}
        onAll={() => onChange([])}
        items={visibleDimensions}
        active={active}
        onToggle={toggle}
        showParentName={false}
        emptyHint={busy ? '标签加载中…' : undefined}
      />

      <SpecTagRow
        label="取值"
        labelHint="与维度行是「且」：只留同时满足这些取值的模板；同维度内多个取值是「或」"
        ariaLabel="模板取值标签"
        allLabel="全部模板取值"
        query={valueQuery}
        onQuery={setValueQuery}
        searchLabel="搜索模板取值标签"
        allActive={selected.length === 0}
        onAll={() => onChange([])}
        items={visibleValues}
        active={active}
        onToggle={toggle}
        showParentName
        emptyHint={busy ? '标签加载中…' : '还没有取值标签'}
      />
    </div>
  );
}

interface SpecTagRowProps {
  label: string;
  /** 这一行的**组合语义**（并进 `title`，悬浮才看 —— 它约束的是这一行）。 */
  labelHint?: string;
  /** 稳定的分组名（第二行标题会随选中项变化，不能拿它当 aria-label） */
  ariaLabel: string;
  allLabel: string;
  query: string;
  onQuery: (value: string) => void;
  searchLabel: string;
  allActive: boolean;
  onAll: () => void;
  items: TagOut[];
  active: Set<string>;
  onToggle: (tag: TagOut) => void;
  showParentName: boolean;
  emptyHint?: string;
}

function SpecTagRow({
  label,
  labelHint,
  ariaLabel,
  allLabel,
  query,
  onQuery,
  searchLabel,
  allActive,
  onAll,
  items,
  active,
  onToggle,
  showParentName,
  emptyHint,
}: SpecTagRowProps) {
  return (
    <div
      role="group"
      aria-label={ariaLabel}
      className="flex min-h-[30px] flex-wrap items-center gap-1.5"
    >
      <span className={ROW_LABEL} title={labelHint ?? label}>
        {label}
      </span>
      <input
        type="search"
        value={query}
        onChange={(event) => onQuery(event.target.value)}
        aria-label={searchLabel}
        placeholder="搜索…"
        className="h-[26px] w-[112px] shrink-0 rounded-md border border-line-soft bg-panel-soft px-2 text-body text-ink placeholder:text-ink-mute focus:border-primary-line focus:outline-none focus:ring-2 focus:ring-primary/10"
      />
      <button
        type="button"
        onClick={onAll}
        aria-label={allLabel}
        aria-pressed={allActive}
        title="清空所有标签筛选"
        className={cn(PILL, allActive ? ACTIVE : IDLE)}
      >
        全部
      </button>
      {items.map((tag) => {
        const isActive = active.has(tag.id);
        const count = tag.specCount ?? 0;
        const full = showParentName ? templateTagLabel(tag) : tag.name;
        return (
          <button
            key={tag.id}
            type="button"
            onClick={() => onToggle(tag)}
            aria-pressed={isActive}
            title={`筛选「${full}」· 当前入口 ${count} 个模板`}
            className={cn(PILL, isActive ? ACTIVE : IDLE, count === 0 && !isActive && 'opacity-60')}
          >
            <span className="max-w-[150px] truncate">{tag.name}</span>
            <span
              className={cn(
                'min-w-[18px] rounded-xs px-1 text-center text-xs tabular-nums',
                isActive ? 'bg-white/25 text-white' : 'bg-panel text-ink-mute',
              )}
            >
              {count}
            </span>
          </button>
        );
      })}
      {!items.length && emptyHint && (
        <span className="text-xs text-ink-mute">{emptyHint}</span>
      )}
    </div>
  );
}
