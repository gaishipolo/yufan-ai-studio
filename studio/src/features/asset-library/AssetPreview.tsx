import { Download, Trash2 } from 'lucide-react';
import { Button, Modal } from '@/shared/ui';
import { formatBytes, formatDateTime } from '@/shared/lib/format';
import { useAssetBlob } from '@/entities/asset/api/asset.queries';
import {
  ASSET_CATEGORY_LABEL,
  ASSET_SOURCE_LABEL,
  ASSET_TYPE_LABEL,
  type Asset,
} from '@/entities/asset/model/types';

export interface AssetPreviewProps {
  asset: Asset | null;
  onClose: () => void;
  /** 传了才显示删除按钮（权限在调用方判定） */
  onDelete?: (asset: Asset) => void;
  /** 正在删除该素材：按钮转 loading，避免连点 */
  deleting?: boolean;
}

/** 素材预览弹窗：按类型给原生播放器，图片直接铺满宽度。 */
export function AssetPreview({ asset, onClose, onDelete, deleting }: AssetPreviewProps) {
  // 原文件同样是带鉴权取回的 Blob（`<img src={fileUrl}>` 带不上令牌，会 401）
  const { url: source, isError, isLoading } = useAssetBlob(asset?.id, 'file');

  return (
    <Modal open={!!asset} onClose={onClose} title={asset?.name} width={720}>
      {asset && (
        <div className="space-y-4 p-4">
          <div className="grid max-h-[52vh] min-h-[180px] place-items-center overflow-hidden rounded-xl bg-[#0d1b21]">
            {isError ? (
              <p className="p-6 text-center text-xs leading-relaxed text-white/70">
                读不到这个文件
                <br />
                <span className="text-white/45">
                  记录还在，但对象存储里没有对应内容（演示数据或已被清理）
                </span>
              </p>
            ) : !source ? (
              <p className="p-6 text-xs text-white/70">
                {isLoading ? '正在读取文件…' : '没有可预览的内容'}
              </p>
            ) : asset.type === 'image' ? (
              <img src={source} alt={asset.name} className="max-h-[52vh] w-full object-contain" />
            ) : asset.type === 'video' ? (
              <video
                src={source}
                controls
                preload="metadata"
                aria-label={asset.name}
                className="max-h-[52vh] w-full"
              >
                <track kind="captions" />
              </video>
            ) : (
              <div className="w-full p-6">
                <audio src={source} controls aria-label={asset.name} className="w-full">
                  <track kind="captions" />
                </audio>
              </div>
            )}
          </div>

          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs sm:grid-cols-3">
            <Meta label="类型" value={ASSET_TYPE_LABEL[asset.type]} />
            <Meta label="来源" value={ASSET_SOURCE_LABEL[asset.source]} />
            {asset.category && (
              <Meta label="归档分类" value={ASSET_CATEGORY_LABEL[asset.category]} />
            )}
            <Meta label="体积" value={formatBytes(asset.sizeBytes)} />
            <Meta label="分辨率" value={`${asset.width} × ${asset.height}`} />
            {asset.duration > 0 && <Meta label="时长" value={`${asset.duration.toFixed(1)} 秒`} />}
            <Meta label="创建于" value={formatDateTime(asset.createdAt)} />
            <Meta label="被引用" value={`${asset.refCount} 次`} />
            <Meta label="格式" value={asset.mimeType} />
          </dl>

          <div className="flex items-center justify-end gap-2 border-t border-line-soft pt-3">
            {/* 下载走 Blob 而不是 fileUrl：直接链出去同样缺 Authorization 头 */}
            {source ? (
              <a
                href={source}
                download={asset.name}
                className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-line bg-panel px-3 text-xs text-ink-soft transition-colors hover:border-line-strong hover:text-ink"
              >
                <Download size={13} />
                下载原文件
              </a>
            ) : (
              <span className="inline-flex h-8 items-center rounded-lg px-3 text-xs text-ink-mute">
                {isError ? '文件不可下载' : '文件读取中…'}
              </span>
            )}
            {onDelete && (
              <Button
                variant="danger"
                icon={<Trash2 size={13} />}
                loading={deleting}
                onClick={() => onDelete(asset)}
              >
                删除
              </Button>
            )}
          </div>
        </div>
      )}
    </Modal>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[11px] text-ink-mute">{label}</dt>
      <dd className="mt-0.5 break-all text-ink">{value}</dd>
    </div>
  );
}
