import { Chip, Modal } from '@/shared/ui';
import { formatBytes, formatDateTime } from '@/shared/lib/format';
import { ARCHIVE_LABELS, archivePreviewUrl, archiveStreamUrl, type ArchivedAsset } from '@/entities/asset/model/archive.types';
import { replacementFitness } from '@/entities/asset/model/archive-fit';
import { ArchivedMedia } from '@/entities/asset/ui/ArchivedMedia';

interface ArchiveDetailProps {
  asset: ArchivedAsset | null;
  onClose: () => void;
}

/**
 * 素材详情。视频播放用派生流，下载用原件地址；
 * 未知规格显示「未知」，不显示 0×0 —— null 和 0 是两件事。
 */
export function ArchiveDetail({asset, onClose}: ArchiveDetailProps) {
  if (!asset) return null;
  const fitness = replacementFitness(asset);
  const playback = asset.type === 'video' ? archivePreviewUrl(archiveStreamUrl(asset.url)) : undefined;
  const specs: [string, string][] = [
    ['分类', ARCHIVE_LABELS[asset.category]],
    ['格式', asset.mimeType || '未知'],
    ['体积', formatBytes(asset.sizeBytes)],
    ['尺寸', asset.width && asset.height ? `${asset.width}×${asset.height}` : '未知'],
    ['时长', asset.duration ? `${asset.duration.toFixed(1)} 秒` : '未知'],
    ['音轨', asset.hasAudio ? '有' : '无'],
    ['创建时间', formatDateTime(asset.createdAt)],
  ];
  return <Modal open onClose={onClose} title={asset.name}>
    <div className="space-y-4 p-4">
      <ArchivedMedia url={asset.url} previewUrl={playback} type={asset.type} name={asset.name} downloadAllowed={asset.downloadAllowed} />
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs sm:grid-cols-3">
        {specs.map(([label, value]) => <div key={label}>
          <dt className="text-ink-mute">{label}</dt>
          <dd className="mt-0.5 break-all text-ink-soft">{value}</dd>
        </div>)}
      </dl>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="text-ink-mute">替换适用性</span>
        {fitness.ok ? <Chip tone="ok">可用于替换</Chip> : <Chip tone="warn">{fitness.reason}</Chip>}
        {!fitness.ok && <span className="text-ink-mute">归档保留，但不能直接用于生成。</span>}
      </div>
      {asset.path && <details className="rounded-lg border border-line p-3 text-xs">
        <summary className="cursor-pointer text-ink-soft">归档信息</summary>
        <p className="mt-2 break-all text-ink-mute">归档位置：{asset.path}</p>
        <p className="mt-1 break-all text-ink-mute">SHA-256：{asset.sha256}</p>
      </details>}
    </div>
  </Modal>;
}
