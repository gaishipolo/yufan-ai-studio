import { useMemo, useState } from 'react';
import { Settings2, X } from 'lucide-react';
import { cn } from '@/shared/lib/cn';
import type { LibraryTagStatOut } from '@/shared/api/schema';
import {
  secondLevelForSelection,
  tagLabel,
  topLevelTags,
} from '@/entities/library/model/tagTree';

export interface LibraryTagBarProps {
  /** 后端已按添加时间排好序；这里再排一次只是防 mock / 旧缓存混进乱序数据 */
  tags: LibraryTagStatOut[];
  /** URL 里选中的 tag id（一级维度 / 二级取值都算，可以跨维度多选） */
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
/* 行标题固定 48px + 截断：两个行标题都是「一级 / 二级」，宽一点会让
   第二行的 pills 挤到换行。 */
const ROW_LABEL = 'w-12 shrink-0 truncate text-body font-medium text-ink-mute';

/**
 * 标签筛选条：**两行**，第一行是**维度**（产品 / 画面类型 / 角色），
 * 第二行是**取值**（6036 / 正面图 / 菜菜）。每行的排布都是
 * 「行标题 → 搜索框 → 全部 → 标签胶囊」。
 *
 * 两级维度模型的组合规则：
 *
 * 1. **同维度多选 = 或**。选「6036」与选「T35」可以并存 —— 两个产品都要看。
 * 2. **跨维度多选 = 且**。选「正面图」与选「6036」并存 = 既是 6036 又是正面图。
 *    这跟旧的三级全并集不同：并集会把「6036 的三视图」也带出来。
 * 3. **同维度里选了维度又选了它下面的取值 → 只留取值**。维度的范围本来就
 *    含它的取值，两个都留着等于「整个产品线」，而用户点「6036」要的是
 *    6036 那一档。收敛发生在发请求之前（`effectiveTagSelection`）。
 * 4. **选中状态不再互相踢掉**。点了就都留着 —— 选中的胶囊全部高亮，
 *    用户看得见自己圈了哪几档；哪一档真正参与筛选由上一条决定。
 *
 * 两行的**二级行随选中项收窄**：点了「产品」，二级行只剩产品的取值；
 * 点了「产品」+「角色」，二级行就是这两条轴的取值之和（口径见
 * `secondLevelForSelection`）。一级行始终列全部维度 —— 维度才是用户想换
 * 方向的地方，收起来就换不了轴了。每行有自己的搜索框，词多了就搜。
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

  const top = useMemo(() => topLevelTags(tags), [tags]);
  /* 二级行只列「当前选中项涉及的维度」下的取值：点了「产品」就只剩产品的
     取值，点了「产品」+「角色」就是两条轴的取值之和。没选任何东西时列全部。 */
  const second = useMemo(
    () => secondLevelForSelection(tags, selected),
    [tags, selected],
  );

  const active = useMemo(() => new Set(selected), [selected]);

  /** 选/不选：点了就加进来，再点一次拿掉。不动同维度的其它标签。 */
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
          同一维度多选是或 · 跨维度是且
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

      {!tags.length && (
        <p className="text-body text-ink-mute">
          {busy ? '标签加载中…' : '还没有标签，给素材打标后会出现在这里'}
        </p>
      )}
      {!!tags.length && !second.length && (
        <p className="text-xs text-ink-mute">
          还没有下级标签，在「管理标签」里把取值挂到维度下面即可
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
