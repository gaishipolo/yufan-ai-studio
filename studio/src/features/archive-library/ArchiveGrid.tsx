import { FolderOpen } from 'lucide-react';
import { Button, CardSkeletonGrid, EmptyState } from '@/shared/ui';
import { ErrorState } from '@/shared/ui/ErrorState';
import { useCan } from '@/shared/access/use-access';
import { ARCHIVE_LABELS, type ArchiveCategory, type ArchivedAsset } from '@/entities/asset/model/archive.types';
import { ArchiveCard } from './ArchiveCard';

interface ArchiveGridProps {
  category: ArchiveCategory;
  assets?: ArchivedAsset[];
  error: unknown;
  isPending: boolean;
  isFetching: boolean;
  onRetry: () => void;
  onPick: () => void;
  onOpen: (asset: ArchivedAsset) => void;
}

/**
 * 列表五分支。判断顺序是这段代码的全部要点：
 * 先分「有没有可用数据」，再分「有没有错误」，最后才轮到空态。
 * 反过来写（先看 items.length）就会让 401、500、断网都显示成「暂无素材」。
 */
export function ArchiveGrid({category, assets, error, isPending, isFetching, onRetry, onPick, onOpen}: ArchiveGridProps) {
  const canCreate = useCan('asset:create');
  const label = ARCHIVE_LABELS[category];
  if (isPending && !assets) {
    return <div role="status" aria-label="正在读取素材">
      <p className="mb-3 text-xs text-ink-mute">正在读取素材…</p>
      <CardSkeletonGrid count={6} />
    </div>;
  }
  if (error && !assets) return <ErrorState error={error} title="素材读取失败" onRetry={onRetry} retrying={isFetching} />;
  return <div className="space-y-3">
    {/* 有错误但还有旧数据时不能说「以下为上次加载的素材」，否则空列表下面什么都没有。 */}
    {!!error && assets && <div role="alert"
      className="flex flex-wrap items-center gap-2 rounded-lg border border-line bg-panel-soft px-3 py-2 text-xs text-ink-soft">
      {assets.length > 0
        ? '更新失败，以下为上次加载的素材。'
        : '更新失败，上一次加载也没有读到任何素材。'}
      <Button size="sm" loading={isFetching} onClick={onRetry}>重试</Button>
    </div>}
    {isFetching && !error && assets && <p role="status" className="text-xs text-ink-mute">正在更新…</p>}
    {assets && assets.length > 0 && <div className="grid grid-cols-[repeat(auto-fill,minmax(190px,1fr))] gap-3">
      {assets.map(asset => <ArchiveCard key={asset.id} asset={asset} onOpen={onOpen} />)}
    </div>}
    {assets && assets.length === 0 && !error && <EmptyState icon={<FolderOpen size={34} />}
      title={`暂无${label}素材`}
      description={canCreate ? '上传文件，或从 inbox 登记已有素材。' : '当前账号没有上传权限，需要补充素材请联系素材维护人员。'}
      action={canCreate ? <Button variant="primary" onClick={onPick}>上传{label}</Button> : undefined} />}
  </div>;
}
