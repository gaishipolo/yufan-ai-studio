import { Chip } from '@/shared/ui';
import { formatBytes } from '@/shared/lib/format';
import { archivePreviewUrl, type ArchivedAsset } from '@/entities/asset/model/archive.types';
import { replacementFitness } from '@/entities/asset/model/archive-fit';
import { ArchivedThumbnail } from '@/entities/asset/ui/ArchivedThumbnail';

interface ArchiveCardProps {
  asset: ArchivedAsset;
  onOpen: (asset: ArchivedAsset) => void;
}

/**
 * 素材卡片。适用性徽标只在原片分类显示：
 * 给每张人物参考图都标「仅原片参与替换」是噪音，而原片正是会被误当"可直接生成"的那一类。
 */
export function ArchiveCard({asset, onOpen}: ArchiveCardProps) {
  const fitness = asset.category === 'source' ? replacementFitness(asset) : null;
  const duration = asset.duration ?? 0;
  const size = asset.width && asset.height ? `${asset.width}×${asset.height}` : '尺寸未知';
  return <button type="button" onClick={() => onOpen(asset)}
    className="overflow-hidden rounded-xl border border-line bg-panel p-3 text-left transition hover:border-primary">
    <div className="mb-3 grid h-24 place-items-center overflow-hidden rounded-lg bg-panel-soft">
      <ArchivedThumbnail url={asset.previewUrl ? archivePreviewUrl(asset.previewUrl) : null} type={asset.type} />
    </div>
    <p className="truncate text-sm font-semibold text-ink" title={asset.name}>{asset.name}</p>
    <p className="mt-1 text-xs text-ink-mute">
      {formatBytes(asset.sizeBytes)} · {duration > 0 ? `${duration.toFixed(1)} 秒` : size}
    </p>
    {fitness && <p className="mt-1.5">
      {fitness.ok ? <Chip tone="ok">可用于替换</Chip> : <Chip tone="warn">{fitness.reason}</Chip>}
    </p>}
  </button>;
}
