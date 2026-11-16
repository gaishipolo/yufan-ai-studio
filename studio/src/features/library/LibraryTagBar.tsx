import { useMemo, useState } from 'react';
import { Settings2, X } from 'lucide-react';
import { cn } from '@/shared/lib/cn';
import type { LibraryTagStatOut } from '@/shared/api/schema';
import {
  ancestorAtDepth,
  ancestorIds,
  childTags,
  descendantIds,
  isTopLevel,
  tagDepth,
  tagLabel,
  tagsAtDepth,
  topLevelTags,
} from '@/entities/library/model/tagTree';

export interface LibraryTagBarProps {
  /** 后端已按添加时间排好序；这里再排一次只是防 mock / 旧缓存混进乱序数据 */
  tags: LibraryTagStatOut[];
  /** URL 里最终选中的 tag id（一级 / 二级 / 三级都算） */
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
  'inline-flex shrink-0 items-center gap-1 rounded-full border px-2.5 py-[3px] text-body leading-4 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40';
const IDLE =
  'border-line-soft bg-panel-soft text-ink-soft hover:border-primary-line hover:bg-primary-soft/60 hover:text-primary-deep';
const ACTIVE = 'border-primary bg-primary text-white shadow-sm';
/* 第二、三行标题会显示当前上级的名字（可能比「二级」长），固定 48px + 截断，
   字号调大后不至于把这一行的 pills 挤到换行。 */
const ROW_LABEL = 'w-12 shrink-0 truncate text-body font-medium text-ink-mute';

/**
 * 标签筛选条：**三行**，第一行一级（分组）、第二行二级（系列）、第三行三级
 * （具体词）。每行的排布都是「行标题 → 搜索框 → 全部 → 标签胶囊」。搜索框紧跟
 * 行标题、靠左放，不推到行尾：三行的输入框上下对齐，用户扫一眼就知道第二个框
 * 只管二级、第三个框只管三级。
 *
 * 层级之间不是简单的三个多选组，有三条口径：
 *
 * 1. 选中一级 = 后端展开成「自身 + 旗下全部下级」，所以点「产品」等于看产品
 *    全部；再点它旗下的「3030」时把「产品」从选中里去掉 —— 否则 OR 叠加后结果
 *    仍是产品全部，用户会以为二级点了没反应。
 * 2. 反过来点一级时清掉它旗下已选的下级（二级、三级都清），避免同一批素材被
 *    冗余条件命中。选到第三级时同链的一级、二级一并移出选中：**一条链上只
 *    选一层**，不然「产品」和「产品 / 3030 / T35」会变成两次重复筛选。
 * 3. **当前一级 / 二级完全从 `selected` 派生**，不存在本地 state。刷新 / 分享
 *    链接后三行仍然对得上，不会出现「第一行没选、第二行却只显示产品子标签」。
 *
 * 计数来自 `GET /library/tags`，后端给的是「自身 + 全部下级」去重后的合计，
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
  const [secondQuery, setSecondQuery] = useState('');
  const [thirdQuery, setThirdQuery] = useState('');

  const byId = useMemo(() => new Map(tags.map((tag) => [tag.id, tag])), [tags]);
  const top = useMemo(() => topLevelTags(tags), [tags]);
  const selectedTags = useMemo(
    () =>
      selected
        .map((id) => byId.get(id))
        .filter((tag): tag is LibraryTagStatOut => !!tag),
    [selected, byId],
  );

  /** 当前展开的一级：优先取已选的一级；否则取已选下级的一级祖先 */
  const activeParent = useMemo(() => {
    const topSelected = selectedTags.filter(isTopLevel);
    if (topSelected.length) return topSelected[topSelected.length - 1].id;
    for (const tag of selectedTags) {
      const root = ancestorAtDepth(tags, tag, 1);
      if (root) return root;
    }
    return '';
  }, [selectedTags, tags]);

  /** 当前展开的二级：优先取已选的二级；否则取已选三级的二级祖先 */
  const activeSecond = useMemo(() => {
    const secondSelected = selectedTags.filter((tag) => tagDepth(tags, tag) === 2);
    if (secondSelected.length) return secondSelected[secondSelected.length - 1].id;
    for (const tag of selectedTags) {
      if (tagDepth(tags, tag) !== 3) continue;
      const parent = ancestorAtDepth(tags, tag, 2);
      if (parent) return parent;
    }
    return '';
  }, [selectedTags, tags]);

  const secondRow = useMemo(
    () => (activeParent ? childTags(tags, activeParent) : tagsAtDepth(tags, 2)),
    [tags, activeParent],
  );
  const thirdRow = useMemo(() => {
    if (activeSecond) return childTags(tags, activeSecond);
    // 还没钻到二级：三级要么全列（没选一级），要么只列当前一级子树下的
    if (!activeParent) return tagsAtDepth(tags, 3);
    return tagsAtDepth(tags, 3).filter(
      (tag) => ancestorAtDepth(tags, tag, 1) === activeParent,
    );
  }, [tags, activeSecond, activeParent]);

  const active = useMemo(() => new Set(selected), [selected]);

  function toggleTop(tag: LibraryTagStatOut) {
    const id = tag.id;
    if (active.has(id)) {
      onChange(selected.filter((item) => item !== id));
      return;
    }
    // 一级 = 整棵子树：旗下已选的二级、三级都要清掉，否则是重复筛选
    const mine = descendantIds(tags, id);
    onChange([...selected.filter((item) => !mine.has(item)), id]);
  }

  /** 二级 / 三级同一条逻辑：选更具体的层级时清掉同链的祖先与下级 */
  function toggleBranch(tag: LibraryTagStatOut) {
    if (active.has(tag.id)) {
      onChange(selected.filter((item) => item !== tag.id));
      return;
    }
    const mine = descendantIds(tags, tag.id);
    const up = ancestorIds(tags, tag);
    onChange([
      ...selected.filter((item) => !mine.has(item) && !up.includes(item)),
      tag.id,
    ]);
  }

  /** 「全部二级标签」= 不限二级及以下；一级不动 */
  function clearSecond() {
    const deeper = new Set(
      selectedTags.filter((tag) => tagDepth(tags, tag) >= 2).map((tag) => tag.id),
    );
    onChange(selected.filter((id) => !deeper.has(id)));
  }

  /** 「全部三级标签」= 只清三级 */
  function clearThird() {
    const third = new Set(
      selectedTags.filter((tag) => tagDepth(tags, tag) === 3).map((tag) => tag.id),
    );
    onChange(selected.filter((id) => !third.has(id)));
  }

  const visibleTop = top.filter((tag) => tag.name.includes(topQuery.trim()));
  const visibleSecond = secondRow.filter((tag) =>
    tagLabel(tag).includes(secondQuery.trim()),
  );
  const visibleThird = thirdRow.filter((tag) =>
    tagLabel(tag).includes(thirdQuery.trim()),
  );
  const hasTopSelection = selectedTags.some(isTopLevel);
  const hasSecondSelection = selectedTags.some((tag) => tagDepth(tags, tag) === 2);
  const hasThirdSelection = selectedTags.some((tag) => tagDepth(tags, tag) === 3);

  return (
    <div className="space-y-1.5" role="group" aria-label="标签筛选">
      <div className="flex items-center gap-1.5">
        <span className="text-body font-semibold text-ink">标签筛选</span>
        {canManage && onManage && (
          <button
            type="button"
            onClick={onManage}
            className="inline-flex items-center gap-1 rounded-md border border-line-soft bg-panel-soft px-1.5 py-[2px] text-xs text-ink-soft transition hover:border-primary-line hover:text-primary-deep"
          >
            <Settings2 size={11} /> 管理标签
          </button>
        )}
        {selected.length > 0 && (
          <button
            type="button"
            onClick={onClear}
            className="inline-flex items-center gap-0.5 text-body text-ink-mute hover:text-primary-deep"
          >
            <X size={11} /> 清除筛选
          </button>
        )}
        <span className="ml-auto shrink-0 text-xs text-ink-mute/80">
          一级是分组 · 逐级下钻到具体词 · 可跨组多选
        </span>
      </div>

      <TagRow
        label="一级"
        ariaLabel="一级标签"
        allLabel="全部标签"
        query={topQuery}
        onQuery={setTopQuery}
        searchLabel="搜索一级标签"
        allActive={!hasTopSelection && !hasSecondSelection && !hasThirdSelection}
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
        query={secondQuery}
        onQuery={setSecondQuery}
        searchLabel="搜索二级标签"
        allActive={!hasSecondSelection && !hasThirdSelection}
        onAll={clearSecond}
        items={visibleSecond}
        active={active}
        onToggle={toggleBranch}
        showParentName={!activeParent}
      />

      <TagRow
        label={activeSecond ? byId.get(activeSecond)?.name ?? '三级' : '三级'}
        ariaLabel="三级标签"
        allLabel="全部三级标签"
        query={thirdQuery}
        onQuery={setThirdQuery}
        searchLabel="搜索三级标签"
        allActive={!hasThirdSelection}
        onAll={clearThird}
        items={visibleThird}
        active={active}
        onToggle={toggleBranch}
        showParentName={!activeSecond}
      />

      {!tags.length && (
        <p className="text-body text-ink-mute">
          {busy ? '标签加载中…' : '还没有标签，给素材打标后会出现在这里'}
        </p>
      )}
      {!!tags.length && !secondRow.length && (
        <p className="text-xs text-ink-mute">
          还没有下级标签，在「管理标签」里把系列挂到一级下面即可
        </p>
      )}
    </div>
  );
}

interface TagRowProps {
  label: string;
  /** 稳定的分组名（第二、三行标题会随当前上级变化，不能拿它当 aria-label） */
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
                'min-w-[18px] rounded-full px-1 text-center text-xs tabular-nums',
                isActive ? 'bg-white/25 text-white' : 'bg-panel text-ink-mute',
              )}
            >
              {count}
            </span>
          </button>
        );
      })}
    </div>
  );
}
