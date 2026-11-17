import { useMemo } from 'react';
import { X } from 'lucide-react';
import type { LibraryTagStatOut } from '@/shared/api/schema';
import { cn } from '@/shared/lib/cn';
import {
  scopeAllowedTags,
  secondLevelForSelection,
  tagLabel,
  tagsAtDepth,
} from '../model/tagTree';

export interface TagFilterProps {
  /** 词表（任意级别：一级维度 / 二级取值） */
  tags: LibraryTagStatOut[];
  /**
   * 允许挑的范围：**作者的候选集**（槽位的标签）。空 = 不限。
   *
   * 范围之外的标签不出现：模板使用者只能在作者圈好的范围里挑，两边看到的
   * 必须是同一批素材。
   */
  scope?: string[];
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
 * 与资产库标签栏的差别只有一处：这里多一个 `scope` —— 作者圈好的候选集，
 * 范围之外的标签不出现。除此之外口径一样：**同维度多选是或、跨维度是且**。
 *
 * 组件只管「挑了哪些」；同一维度里「维度与取值只留取值」这件事由调用方
 * 用 `effectiveTagSelection` 收敛（这里保留用户点的全部，让胶囊都高亮）。
 */
export function TagFilter({
  tags,
  scope = [],
  value,
  onChange,
  title = '按标签筛选',
}: TagFilterProps) {
  const allowed = useMemo(() => scopeAllowedTags(tags, scope), [tags, scope]);
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
