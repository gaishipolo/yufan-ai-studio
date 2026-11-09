import { LayoutGrid, List, RefreshCw, Upload } from 'lucide-react';
import { Button, Select } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import { DENSITIES, SORTS, type Density } from './model/controls';

/**
 * 第④段：吸顶控制条。
 *
 * 吸顶不是为了好看：素材列表一屏放不下，翻到第三屏想换排序时如果必须滚回顶部，
 * 用户会改用浏览器搜索——那说明界面已经不可用了。
 *
 * 这里**不重复分类与标签**。它们在②③段，各自有明确的位置；把它们塞进控制条会让
 * 一个横条同时承担"筛选"和"动作"，而多选时整条会被批量操作替换掉——
 * 那时筛选条件会跟着消失，用户以为筛选被清了。
 */
export function StickyControls({
  total,
  density,
  sort,
  isFetching,
  onDensity,
  onSort,
  onRefresh,
  onUpload,
  uploadDisabled,
  uploadLabel = '上传素材',
}: {
  total?: number;
  density: Density;
  sort: string;
  isFetching: boolean;
  onDensity: (next: Density) => void;
  onSort: (next: string) => void;
  onRefresh: () => void;
  onUpload?: () => void;
  uploadDisabled?: boolean;
  uploadLabel?: string;
}) {
  return (
    <div className="sticky top-0 z-10 -mx-1 flex flex-wrap items-center gap-2 border-b border-line-soft bg-canvas/95 px-1 py-1.5 backdrop-blur">
      <span className="text-xs text-ink-soft">
        {typeof total === 'number' ? `共 ${total} 项` : '共 — 项'}
      </span>
      <Select
        aria-label="排序"
        value={sort}
        options={SORTS.map((item) => ({value: item.value, label: item.label}))}
        onChange={(event) => onSort(event.target.value)}
      />
      <div className="flex items-center gap-1" role="group" aria-label="视图密度">
        {DENSITIES.map((item) => {
          const Icon = item.value === 'full' ? LayoutGrid : List;
          const active = density === item.value;
          return (
            <button
              key={item.value}
              type="button"
              aria-pressed={active}
              title={item.label}
              onClick={() => onDensity(item.value)}
              className={cn(
                'inline-flex h-7 items-center gap-1 rounded-lg border px-2 text-xs',
                active
                  ? 'border-primary-line bg-primary-soft text-primary-deep'
                  : 'border-line bg-panel text-ink-soft hover:border-line-strong',
              )}
            >
              <Icon size={13} aria-hidden />
              {item.label}
            </button>
          );
        })}
      </div>
      <div className="ml-auto flex items-center gap-1.5">
        <Button size="sm" variant="secondary" loading={isFetching} onClick={onRefresh}>
          <RefreshCw size={13} aria-hidden />
          刷新
        </Button>
        {onUpload && (
          <Button size="sm" disabled={uploadDisabled} onClick={onUpload}>
            <Upload size={13} aria-hidden />
            {uploadLabel}
          </Button>
        )}
      </div>
    </div>
  );
}
