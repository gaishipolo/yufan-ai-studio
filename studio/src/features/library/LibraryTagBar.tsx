import { useMemo, useState } from 'react';
import { Settings2, X } from 'lucide-react';
import { cn } from '@/shared/lib/cn';
import type { LibraryTagStatOut } from '@/shared/api/schema';
import {
  childTags,
  isTopLevel,
  tagLabel,
  topLevelTags,
} from '@/entities/library/model/tagTree';

export interface LibraryTagBarProps {
  /** 后端已按添加时间排好序；这里再排一次只是防 mock / 旧缓存混进乱序数据 */
  tags: LibraryTagStatOut[];
  /** URL 里最终选中的 tag id（一级或二级都算） */
  selected: string[];
  busy?: boolean;
  /** 交回**完整**的选中数组：父子互斥的取舍在组件里算，容器只负责写 URL */
  onChange: (next: string[]) => void;
  onClear: () => void;
  /** 有 `library:tag:manage` 时才给管理入口 */
  onManage?: () => void;
  canManage?: boolean;
}

const PILL =
  'inline-flex shrink-0 items-center gap-1 rounded-full border px-2.5 py-[3px] text-[13px] leading-4 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40';
const IDLE =
  'border-line-soft bg-panel-soft text-ink-soft hover:border-primary-line hover:bg-primary-soft/60 hover:text-primary-deep';
const ACTIVE = 'border-primary bg-primary text-white shadow-sm';
/* 第二行标题会显示当前一级的名字（可能比「二级」长），固定 48px + 截断，
   字号调大后不至于把这一行的 pills 挤到换行。 */
const ROW_LABEL = 'w-12 shrink-0 truncate text-[13px] font-medium text-ink-mute';

/**
 * 标签筛选条：**两行**，第一行一级、第二行二级，每行各自「全部 + 搜索」。
 *
 * 两级之间不是简单的两个多选组，有三条口径：
 *
 * 1. 选中一级 = 后端展开成「自身 + 旗下二级」，所以点「产品」等于看产品全部；
 *    再点它旗下的「3030」时把「产品」从选中里去掉 —— 否则 OR 叠加后结果
 *    仍是产品全部，用户会以为二级点了没反应。
 * 2. 反过来点一级时清掉它旗下已选的二级，避免同一批素材被冗余条件命中。
 * 3. **当前一级完全从 `selected` 派生**，不存在本地 state。刷新 / 分享链接
 *    后两行仍然对得上，不会出现「第一行没选、第二行却只显示产品子标签」。
 *
 * 计数来自 `GET /library/tags`，一级的数字已经是「自身 + 子级」去重后的合计，
 * 与点进去看到的条数一致。
 */
export function LibraryTagBar({
  tags,
  selected,
  busy,
  onChange,
  onClear,
  onManage,
  canManage,
}: LibraryTagBarProps) {
  const [topQuery, setTopQuery] = useState('');
  const [childQuery, setChildQuery] = useState('');

  const byId = useMemo(() => new Map(tags.map((tag) => [tag.id, tag])), [tags]);
  const top = useMemo(() => topLevelTags(tags), [tags]);
  const children = useMemo(() => childTags(tags), [tags]);

  /** 选中的一级优先；否则取第一个选中二级的父；都没有就显示全部二级 */
  const activeParent = useMemo(() => {
    const topSelected = selected.filter((id) => {
      const tag = byId.get(id);
      return tag ? isTopLevel(tag) : false;
    });
    if (topSelected.length) return topSelected[topSelected.length - 1];
    for (const id of selected) {
      const parent = byId.get(id)?.parentId;
      if (parent) return parent;
    }
    return '';
  }, [selected, byId]);

  const secondRow = useMemo(
    () => childTags(tags, activeParent || undefined),
    [tags, activeParent],
  );
  const active = useMemo(() => new Set(selected), [selected]);
  const childIds = useMemo(() => new Set(children.map((tag) => tag.id)), [children]);

  function toggleTop(tag: LibraryTagStatOut) {
    const id = tag.id;
    if (active.has(id)) {
      onChange(selected.filter((item) => item !== id));
      return;
    }
    const mine = new Set(children.filter((item) => item.parentId === id).map((item) => item.id));
    onChange([...selected.filter((item) => !mine.has(item)), id]);
  }

  function toggleChild(tag: LibraryTagStatOut) {
    if (active.has(tag.id)) {
      onChange(selected.filter((item) => item !== tag.id));
      return;
    }
    // 二级比一级更具体：把父从选中里拿掉，避免 OR 叠加后等于没筛
    onChange([...selected.filter((item) => item !== tag.parentId), tag.id]);
  }

  const visibleTop = top.filter((tag) => tag.name.includes(topQuery.trim()));
  const visibleChildren = secondRow.filter((tag) =>
    tagLabel(tag).includes(childQuery.trim()),
  );
  const hasTopSelection = selected.some((id) => {
    const tag = byId.get(id);
    return tag ? isTopLevel(tag) : false;
  });
  const hasChildSelection = selected.some((id) => childIds.has(id));

  return (
    <div className="space-y-1.5" role="group" aria-label="标签筛选">
      <div className="flex items-center gap-1.5">
        <span className="text-[13px] font-semibold text-ink">标签筛选</span>
        {canManage && onManage && (
          <button
            type="button"
            onClick={onManage}
            className="inline-flex items-center gap-1 rounded-md border border-line-soft bg-panel-soft px-1.5 py-[2px] text-[12.5px] text-ink-soft transition hover:border-primary-line hover:text-primary-deep"
          >
            <Settings2 size={11} /> 管理标签
          </button>
        )}
        {selected.length > 0 && (
          <button
            type="button"
            onClick={onClear}
            className="inline-flex items-center gap-0.5 text-[13px] text-ink-mute hover:text-primary-deep"
          >
            <X size={11} /> 清除筛选
          </button>
        )}
        <span className="ml-auto shrink-0 text-[12.5px] text-ink-mute/80">
          一级为分组 · 点二级看具体系列 · 可跨组多选
        </span>
      </div>

      <TagRow
        label="一级"
        ariaLabel="一级标签"
        allLabel="全部标签"
        query={topQuery}
        onQuery={setTopQuery}
        searchLabel="搜索一级标签"
        allActive={!hasTopSelection && !hasChildSelection}
        onAll={onClear}
        items={visibleTop}
        active={active}
        onToggle={toggleTop}
        showParentName={false}
      />

      <TagRow
        label={activeParent ? byId.get(activeParent)?.name ?? '二级' : '二级'}
        ariaLabel="二级标签"
        allLabel="全部二级标签"
        query={childQuery}
        onQuery={setChildQuery}
        searchLabel="搜索二级标签"
        allActive={!hasChildSelection}
        onAll={() => onChange(selected.filter((id) => !childIds.has(id)))}
        items={visibleChildren}
        active={active}
        onToggle={toggleChild}
        showParentName={!activeParent}
      />

      {!tags.length && (
        <p className="text-[13px] text-ink-mute">
          {busy ? '标签加载中…' : '还没有标签，给素材打标后会出现在这里'}
        </p>
      )}
      {!!tags.length && !children.length && (
        <p className="text-[12.5px] text-ink-mute">
          还没有二级标签，在「管理标签」里把系列挂到一级下面即可
        </p>
      )}
    </div>
  );
}

interface TagRowProps {
  label: string;
  /** 稳定的分组名（第二行标题会随当前一级变化，不能拿它当 aria-label） */
  ariaLabel: string;
  allLabel: string;
  query: string;
  onQuery: (value: string) => void;
  searchLabel: string;
  allActive: boolean;
  onAll: () => void;
  items: LibraryTagStatOut[];
  active: Set<string>;
  onToggle: (tag: LibraryTagStatOut) => void;
  showParentName: boolean;
}

function TagRow({
  label,
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
}: TagRowProps) {
  return (
    <div
      role="group"
      aria-label={ariaLabel}
      className="flex min-h-[30px] flex-wrap items-center gap-1.5"
    >
      <span className={ROW_LABEL} title={label}>
        {label}
      </span>
      <button
        type="button"
        onClick={onAll}
        aria-label={allLabel}
        aria-pressed={allActive}
        className={cn(PILL, allActive ? ACTIVE : IDLE)}
      >
        全部
      </button>
      {items.map((tag) => {
        const count = tag.assetCount ?? 0;
        const isActive = active.has(tag.id);
        const text = showParentName ? tagLabel(tag) : tag.name;
        return (
          <button
            key={tag.id}
            type="button"
            onClick={() => onToggle(tag)}
            aria-pressed={isActive}
            title={`筛选「${text}」· 当前入口 ${count} 条`}
            className={cn(PILL, isActive ? ACTIVE : IDLE, count === 0 && !isActive && 'opacity-60')}
          >
            <span className="max-w-[150px] truncate">{text}</span>
            <span
              className={cn(
                'min-w-[18px] rounded-full px-1 text-center text-[12px] tabular-nums',
                isActive ? 'bg-white/25 text-white' : 'bg-panel text-ink-mute',
              )}
            >
              {count}
            </span>
          </button>
        );
      })}
      <input
        type="search"
        value={query}
        onChange={(event) => onQuery(event.target.value)}
        aria-label={searchLabel}
        placeholder="搜索…"
        className="ml-auto h-[26px] w-[112px] shrink-0 rounded-md border border-line-soft bg-panel-soft px-2 text-[13px] text-ink placeholder:text-ink-mute focus:border-primary-line focus:outline-none focus:ring-2 focus:ring-primary/10"
      />
    </div>
  );
}
