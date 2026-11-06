import { Trash2 } from 'lucide-react';
import { CoverImage, IconButton } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import { formatBytes, formatDateTime } from '@/shared/lib/format';
import { useAssetObjectUrl } from '../api/asset.queries';
import { resolveCoverSource } from '../model/cover';
import { ASSET_TYPE_LABEL, type Asset } from '../model/types';

export interface AssetCardProps {
  asset: Asset;
  onOpen: (asset: Asset) => void;
  /** 传了才显示删除按钮：没权限时不渲染，避免点下去才 403 */
  onDelete?: (asset: Asset) => void;
  className?: string;
}

export function AssetCard({ asset, onOpen, onDelete, className }: AssetCardProps) {
  // 缩略图走带鉴权的 Blob（见 `model/cover.ts`）：托管封面是站内相对路径，
  // 远端封面是绝对地址，直接加载。`coverUrl` 为空说明后端根本没有封面
  // （没有首帧的视频/音频），这时不发请求：否则每张卡都会 404 一次，控制台一片红。
  const cover = resolveCoverSource(asset);
  const blob = useAssetObjectUrl(cover.blobId, 'cover');

  return (
    <article
      className={cn(
        'group relative overflow-hidden rounded-card border border-line-soft bg-panel transition-all duration-200',
        'hover:-translate-y-0.5 hover:border-line-strong hover:shadow-card',
        className,
      )}
    >
      {/* 点击区域是整张卡片，删除按钮是它的**兄弟**节点而不是子节点：
          按钮里套按钮在 HTML 里是无效结构，键盘 Tab 也会出现两个焦点。 */}
      <button type="button" onClick={() => onOpen(asset)} className="block w-full text-left">
        <div className="relative">
          <CoverImage
            src={blob || cover.directUrl || undefined}
            seed={asset.id}
            alt={asset.name}
            className="aspect-square w-full"
            imgClassName="transition-transform duration-300 group-hover:scale-[1.03]"
          />
          <span className="absolute left-[7px] top-[7px] rounded-[5px] bg-[rgba(10,22,28,.55)] px-1.5 py-px text-[10px] text-white backdrop-blur-[4px]">
            {ASSET_TYPE_LABEL[asset.type]}
          </span>
        </div>
        <div className="px-2.5 pb-2.5 pt-2">
          <p className="truncate text-[11.5px] text-ink" title={asset.name}>
            {asset.name}
          </p>
          <div className="mt-1 flex items-center justify-between text-[10.5px] text-ink-mute">
            <span>{formatBytes(asset.sizeBytes)}</span>
            <span>{formatDateTime(asset.createdAt)}</span>
          </div>
        </div>
      </button>

      {onDelete && (
        <IconButton
          icon={<Trash2 size={13} />}
          label={`删除 ${asset.name}`}
          onClick={() => onDelete(asset)}
          className={cn(
            'absolute right-1.5 top-1.5 text-white/90 hover:bg-[rgba(217,64,54,.9)] hover:text-white',
            'bg-[rgba(10,22,28,.45)] opacity-0 backdrop-blur-[4px] group-hover:opacity-100 focus-visible:opacity-100',
          )}
        />
      )}
    </article>
  );
}
