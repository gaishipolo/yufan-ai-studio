import { useMemo, useState } from 'react';
import { Settings2, X } from 'lucide-react';
import { cn } from '@/shared/lib/cn';
import type { LibraryTagStatOut } from '@/shared/api/schema';
import { sortByCreated, tagLabel } from '@/entities/library/model/tagTree';
import { tagSelection, toggleTagSelection } from '@/shared/lib/tagSelection';

export interface LibraryTagBarProps {
  /** 后端已按添加时间排好序；这里再排一次只是防 mock / 旧缓存混进乱序数据 */
  tags: LibraryTagStatOut[];
  /** URL 里选中的 tag id：最多一个维度 + 一个取值（取值默认带着父维度） */
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
  'inline-flex shrink-0 items-center gap-1 rounded-xs border px-2.5 py-[3px] text-body leading-4 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40';
const IDLE =
  'border-line-soft bg-panel-soft text-ink-soft hover:border-primary-line hover:bg-primary-soft/60 hover:text-primary-deep';
const ACTIVE = 'border-primary bg-primary text-white shadow-sm';
/* 行标题固定 48px + 截断：两个行标题都是「一级 / 二级」，宽一点会让
   第二行的 pills 挤到换行。 */
const ROW_LABEL = 'w-12 shrink-0 truncate text-body font-medium text-ink-mute';

/**
 * 标签筛选条：**两行**，第一行是**维度**（产品 / 画面类型 / 角色），
 * 第二行是**取值**（6036 / 正面图 / 菜菜）。每行的排布都是
 * 「行标题 → 搜索框 → 全部 → 标签胶囊」。
 *
 * **每层最多一个**：一级维度至多一个、二级取值至多一个。
 * 点一级会切换维度并清掉旧取值；点二级会自动选中它的父维度（一级保持高亮），
 * 同级再点另一个会替换；点已选中的二级只取消取值，父维度仍在。
 *
 * 二级行随选中的一级收窄：选了「产品」，二级行只剩产品的取值。
 * 一级行始终列全部维度，换方向不用先清空。每行有自己的搜索框。
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

  const ordered = useMemo(() => sortByCreated(tags), [tags]);
  const selection = useMemo(() => tagSelection(ordered, selected), [ordered, selected]);
  const top = selection.dimensions;
  const second = selection.values;
  const active = selection.activeIds;

  function toggle(tag: LibraryTagStatOut) {
    onChange(toggleTagSelection(ordered, selected, tag));
  }

  const visibleTop = top.filter((tag) => tag.name.includes(topQuery.trim()));
  const visibleSecond = second.filter((tag) =>
    tagLabel(tag).includes(secondQuery.trim()),
  );

  return (
    <div className="space-y-1.5" role="group" aria-label="标签筛选">
      <div className="flex items-center gap-1.5">
        <span className="text-body font-semibold text-ink">标签筛选</span>
        {canManage && onManage && (
          <button
            type="button"
            onClick={onManage}
            className="inline-flex items-center gap-1 rounded-pill border border-line-soft bg-panel-soft px-1.5 py-[2px] text-xs text-ink-soft transition hover:border-primary-line hover:text-primary-deep"
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
      </div>

      {/* 行标题用**用户视角的概念**（「维度」/「取值」），不用「一级」「二级」——
          「一级」「二级」是存储结构的说法：看标签词（「产品」「6036」「正面图」）
          的人分不出哪行是分类、哪行是内容。

          单选规则（点别的替换当前、再点取消）并进行标题的 `title`：
          悬浮才看，常驻版面干净。 */}
      <TagRow
        label="维度"
        labelHint="单选：点别的维度会替换并清掉已选取值"
        ariaLabel="维度标签"
        allLabel="全部标签"
        query={topQuery}
        onQuery={setTopQuery}
        searchLabel="搜索维度标签"
        allActive={selected.length === 0}
        onAll={onClear}
        items={visibleTop}
        active={active}
        onToggle={toggle}
        showParentName={false}
      />

      <TagRow
        label="取值"
        labelHint="单选：点二级会自动选中父维度；再点取消取值"
        ariaLabel="取值标签"
        allLabel="全部标签（取值行）"
        query={secondQuery}
        onQuery={setSecondQuery}
        searchLabel="搜索取值标签"
        allActive={selected.length === 0}
        onAll={onClear}
        items={visibleSecond}
        active={active}
        onToggle={toggle}
        showParentName
      />

      {!tags.length && (
        <p className="text-body text-ink-mute">
          {busy ? '标签加载中…' : '还没有标签，给素材打标后会出现在这里'}
        </p>
      )}
      {!!tags.length && !second.length && (
        <p className="text-xs text-ink-mute">还没有取值标签</p>
      )}
    </div>
  );
}

interface TagRowProps {
  label: string;
  /** 这一行的**组合语义**（并进 `title`，悬浮才看 —— 它约束的是这一行）。 */
  labelHint?: string;
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
}: TagRowProps) {
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
        title="清空所有层级的标签筛选"
        className={cn(PILL, allActive ? ACTIVE : IDLE)}
      >
        全部
      </button>
      {items.map((tag) => {
        const count = tag.assetCount ?? 0;
        const isActive = active.has(tag.id);
        /* 显示用短名，悬停说明用完整路径（用户 2026-11-17 拍板）：
           胶囊上写「正面图 / 正面图 / 正面图」这种前缀太丑，
           但鼠标移上去仍要看得见它挂在哪条轴上。 */
        const text = tag.name;
        const full = showParentName ? tagLabel(tag) : tag.name;
        return (
          <button
            key={tag.id}
            type="button"
            onClick={() => onToggle(tag)}
            aria-pressed={isActive}
            title={`筛选「${full}」· 当前入口 ${count} 条`}
            className={cn(PILL, isActive ? ACTIVE : IDLE, count === 0 && !isActive && 'opacity-60')}
          >
            <span className="max-w-[150px] truncate">{text}</span>
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
    </div>
  );
}
