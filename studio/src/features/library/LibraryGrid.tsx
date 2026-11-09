import { FolderOpen, SearchX } from 'lucide-react';
import { Button, CardSkeletonGrid, EmptyState, ErrorState } from '@/shared/ui';
import type { LibraryAssetOut } from '@/shared/api/schema';
import { viewMeta, type LibraryView } from '@/entities/library/model/types';
import { LibraryCard, type LibraryCardAction } from './LibraryCard';

export interface LibraryGridProps {
  view: LibraryView;
  items?: LibraryAssetOut[];
  selected: Set<string>;
  viewer: { id: string; canDeleteAny: boolean };
  isPending: boolean;
  error: Error | null;
  hasFilters: boolean;
  onRetry: () => void;
  onToggle: (id: string) => void;
  onToggleAll: (ids: string[], checked: boolean) => void;
  onAction: (action: LibraryCardAction, asset: LibraryAssetOut) => void;
}

/**
 * 列表三态都在这里：加载中 / 空 / 失败。
 *
 * 「空」分两种文案：有筛选条件时说「没有匹配的资产」（并给清空筛选的入口），
 * 没筛选时才说「这个入口还是空的」。把两者混成一句，用户会以为数据丢了。
 */
export function LibraryGrid(props: LibraryGridProps) {
  const { view, items, selected, isPending, error, hasFilters, onRetry } = props;
  const meta = viewMeta(view);

  if (error) {
    return <ErrorState error={error} onRetry={onRetry} />;
  }

  if (isPending && !items) {
    return <CardSkeletonGrid count={8} />;
  }

  if (!items?.length) {
    return (
      <div className="rounded-card border border-line-soft bg-panel">
        <EmptyState
          icon={hasFilters ? <SearchX size={26} /> : <FolderOpen size={26} />}
          title={hasFilters ? '没有匹配的资产' : `${meta.label}还是空的`}
          description={
            hasFilters
              ? '换一个类型或标签，或者清空筛选条件再看一次。'
              : meta.hint
          }
        />
      </div>
    );
  }

  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(196px,1fr))] gap-3.5">
      {items.map((asset) => (
        <LibraryCard
          key={asset.id}
          asset={asset}
          view={view}
          selected={selected.has(asset.id)}
          viewer={props.viewer}
          onToggle={props.onToggle}
          onAction={props.onAction}
        />
      ))}
    </div>
  );
}

/** 顶部「全选当前页」：只在有内容时出现，省得空列表还挂一个没意义的控件 */
export function LibrarySelectAll({
  items,
  selected,
  onToggleAll,
}: {
  items?: LibraryAssetOut[];
  selected: Set<string>;
  onToggleAll: (ids: string[], checked: boolean) => void;
}) {
  if (!items?.length) return null;
  const ids = items.map((item) => item.id);
  const all = ids.every((id) => selected.has(id));
  return (
    <Button size="sm" variant="ghost" onClick={() => onToggleAll(ids, !all)}>
      {all ? '取消全选本页' : '全选本页'}
    </Button>
  );
}
