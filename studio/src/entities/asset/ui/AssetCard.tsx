import { CoverImage } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import { formatBytes } from '@/shared/lib/format';
import { ASSET_TYPE_LABEL, type Asset } from '../model/types';

export function AssetCard({ asset, className }: { asset: Asset; className?: string }) {
  return (
    <article
      className={cn(
        'group cursor-pointer overflow-hidden rounded-card border border-line-soft bg-panel transition-all duration-200',
        'hover:-translate-y-0.5 hover:border-line-strong hover:shadow-card',
        className,
      )}
    >
      <div className="relative">
        <CoverImage
          src={asset.coverUrl}
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
        <p className="truncate text-[11.5px] text-ink">{asset.name}</p>
        <div className="mt-1 flex items-center justify-between text-[10.5px] text-ink-mute">
          <span>{formatBytes(asset.sizeBytes)}</span>
          <span>引用 {asset.refCount}</span>
        </div>
      </div>
    </article>
  );
}
