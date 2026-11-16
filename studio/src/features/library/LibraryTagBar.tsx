import { useMemo, useState } from 'react';
import { Settings2, X } from 'lucide-react';
import { cn } from '@/shared/lib/cn';
import type { LibraryTagStatOut } from '@/shared/api/schema';
import {
  tagLabel,
  tagsAtDepth,
  topLevelTags,
} from '@/entities/library/model/tagTree';

export interface LibraryTagBarProps {
  /** 后端已按添加时间排好序；这里再排一次只是防 mock / 旧缓存混进乱序数据 */
  tags: LibraryTagStatOut[];
  /** URL 里选中的 tag id（一级 / 二级 / 三级都算，可以跨链多选） */
  selected: string[];
  busy?: boolean;
  /** 交回**完整**的选中数组：容器只负责写 URL，取舍在组件里算 */
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
/* 行标题固定 48px + 截断：三个行标题都是「一级 / 二级 / 三级」，宽一点会让
   第二、三行的 pills 挤到换行。 */
const ROW_LABEL = 'w-12 shrink-0 truncate text-body font-medium text-ink-mute';

/**
 * 标签筛选条：**三行**，第一行一级（分组）、第二行二级（系列）、第三行三级
 * （具体词）。每行的排布都是「行标题 → 搜索框 → 全部 → 标签胶囊」。
 *
 * 三级标签现在可以**任意组合**：
 *
 * 1. **跨链多选 = 并集**。选「产品」与选「角色」可以并存（两个一级分组），
 *    选「产品 / 3030」与选「角色 / 菜菜」也可以并存 —— 结果是两边范围的并集。
 *    后端一直是 OR（选中标签展开成「自身 + 全部下级」），这里只是把「只能选
 *    一条链」的界面限制拆掉。
 * 2. **同一条链上只按最具体的那一层筛选**。父的范围本来就含子：既选「产品」
 *    又选「产品 / 3030」，用户要的是 3030 那一档，不是整个产品线。收敛发生在
 *    发请求之前（`effectiveTagSelection`），后端那边仍是一律 OR。
 * 3. **选中状态不再互相踢掉**。以前点二级会把父一级从选中里移除，现在点了
 *    就都留着 —— 选中的胶囊全部高亮，用户看得见自己圈了哪几档；哪一档真正
 *    参与筛选由上一条决定。
 *
 * 三行**始终列全部标签**（不按选中项收拢）：词表只有几十条，收起来反而让
 * 「另一条链上的词」点不到。每行有自己的搜索框，词多了就搜。
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

  const top = useMemo(() => topLevelTags(tags), [tags]);
  const second = useMemo(() => tagsAtDepth(tags, 2), [tags]);
  const third = useMemo(() => tagsAtDepth(tags, 3), [tags]);

  const active = useMemo(() => new Set(selected), [selected]);

  /** 选/不选：点了就加进来，再点一次拿掉。不动同链的其它标签。 */
  function toggle(tag: LibraryTagStatOut) {
    onChange(
      active.has(tag.id)
        ? selected.filter((item) => item !== tag.id)
        : [...selected, tag.id],
    );
  }

  const visibleTop = top.filter((tag) => tag.name.includes(topQuery.trim()));
  const visibleSecond = second.filter((tag) =>
    tagLabel(tag).includes(secondQuery.trim()),
  );
  const visibleThird = third.filter((tag) =>
    tagLabel(tag).includes(thirdQuery.trim()),
  );

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
          三个级别可任意组合 · 跨组多选是并集
        </span>
      </div>

      <TagRow
        label="一级"
        ariaLabel="一级标签"
        allLabel="全部标签"
        query={topQuery}
        onQuery={setTopQuery}
        searchLabel="搜索一级标签"
        allActive={selected.length === 0}
        onAll={onClear}
        items={visibleTop}
        active={active}
        onToggle={toggle}
        showParentName={false}
      />

      <TagRow
        label="二级"
        ariaLabel="二级标签"
        allLabel="全部标签（二级行）"
        query={secondQuery}
        onQuery={setSecondQuery}
        searchLabel="搜索二级标签"
        allActive={selected.length === 0}
        onAll={onClear}
        items={visibleSecond}
        active={active}
        onToggle={toggle}
        showParentName
      />

      <TagRow
        label="三级"
        ariaLabel="三级标签"
        allLabel="全部标签（三级行）"
        query={thirdQuery}
        onQuery={setThirdQuery}
        searchLabel="搜索三级标签"
        allActive={selected.length === 0}
        onAll={onClear}
        items={visibleThird}
        active={active}
        onToggle={toggle}
        showParentName
      />

      {!tags.length && (
        <p className="text-body text-ink-mute">
          {busy ? '标签加载中…' : '还没有标签，给素材打标后会出现在这里'}
        </p>
      )}
      {!!tags.length && !second.length && !third.length && (
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
        title="清空所有层级的标签筛选"
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
