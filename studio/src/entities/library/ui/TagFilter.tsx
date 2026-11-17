import { useMemo } from 'react';
import { X } from 'lucide-react';
import type { LibraryTagStatOut } from '@/shared/api/schema';
import { cn } from '@/shared/lib/cn';
import {
  secondLevelForSelection,
  tagLabel,
  tagsAtDepth,
} from '../model/tagTree';

export interface TagFilterProps {
  /** 词表（任意级别：一级维度 / 二级取值） */
  tags: LibraryTagStatOut[];
  /**
   * 只列**这批素材身上实际有**的标签（`assetCount > 0`）。
   *
   * 模板库用：作者圈了候选集之后，要显示的是「这批素材实际有的二级标签」。
   * 列出一堆这批素材一个都没挂的标签，用户点了只得到空列表 ——
   * 那是词表的全貌，不是这批素材的。
   */
  onlyWithAssets?: boolean;
  /** 已挑的标签 id（可空） */
  value: string[];
  onChange: (ids: string[]) => void;
  /** 行标题 */
  title?: string;
}

const PILL =
  'inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-[2px] text-micro leading-4 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40';
const IDLE =
  'border-line-soft bg-panel-soft text-ink-soft hover:border-primary-line hover:bg-primary-soft/60 hover:text-primary-deep';
const ACTIVE = 'border-primary bg-primary text-white shadow-sm';

/**
 * 标签筛选条（选择器用）：**两行**，一级维度 / 二级取值。
 *
 * 口径与资产库标签栏一致：**同维度多选是或、跨维度是且**。
 *
 * 组件只管「挑了哪些」；同一维度里「维度与取值只留取值」这件事由调用方
 * 用 `effectiveTagSelection` 收敛（这里保留用户点的全部，让胶囊都高亮）。
 */
export function TagFilter({
  tags,
  onlyWithAssets = false,
  value,
  onChange,
  title = '按标签筛选',
}: TagFilterProps) {
  /* 只留有素材的标签：词表是全的，而这批素材只挂了其中几个。 */
  const allowed = useMemo(
    () => (onlyWithAssets ? tags.filter((tag) => (tag.assetCount ?? 0) > 0) : tags),
    [tags, onlyWithAssets],
  );
  const rows = useMemo(
    () => [
      { label: '一级', items: tagsAtDepth(allowed, 1) },
      /* 二级行随选中项收窄：点了「产品」就只剩产品的取值，
         点了「产品」+「角色」就是两条轴的取值之和。 */
      { label: '二级', items: secondLevelForSelection(allowed, value) },
    ],
    [allowed, value],
  );
  const active = useMemo(() => new Set(value), [value]);

  function toggle(id: string) {
    onChange(active.has(id) ? value.filter((item) => item !== id) : [...value, id]);
  }

  if (!allowed.length) return null;

  return (
    <div className="space-y-1" role="group" aria-label={title}>
      <div className="flex items-center gap-1.5">
        <span className="text-meta text-ink-mute">{title}</span>
        {value.length > 0 && (
          <button
            type="button"
            onClick={() => onChange([])}
            aria-label="清除标签筛选"
            className="inline-flex items-center gap-0.5 text-micro text-ink-mute hover:text-primary-deep"
          >
            <X size={10} /> 清除
          </button>
        )}
        <span className="ml-auto text-micro text-ink-mute/80">
          同一维度多选是或 · 跨维度是且
        </span>
      </div>
      {rows.map((row) =>
        row.items.length ? (
          <div
            key={row.label}
            role="group"
            aria-label={`${row.label}标签`}
            className="flex flex-wrap items-center gap-1"
          >
            <span className="w-8 shrink-0 text-micro text-ink-mute">{row.label}</span>
            {row.items.map((tag) => {
              const isActive = active.has(tag.id);
              /* 显示短名，悬停说明给完整路径 —— 与标签栏同一口径。 */
              const short = tag.name;
              const full = tagLabel(tag);
              return (
                <button
                  key={tag.id}
                  type="button"
                  onClick={() => toggle(tag.id)}
                  aria-pressed={isActive}
                  title={full}
                  className={cn(PILL, isActive ? ACTIVE : IDLE)}
                >
                  <span className="max-w-[140px] truncate">{short}</span>
                </button>
              );
            })}
          </div>
        ) : null,
      )}
    </div>
  );
}
