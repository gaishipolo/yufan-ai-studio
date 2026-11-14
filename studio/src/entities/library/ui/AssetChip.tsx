import { X } from 'lucide-react';
import { useLibraryDetail } from '@/entities/library/api/library.queries';
import { CoverImage } from '@/shared/ui';

/**
 * 素材 chip：缩略图 + 名字（+ 可选移除）。
 *
 * 每个 id 一条详情查询 —— React Query 按 id 缓存，切模板或换标签时不会重复拉。
 * 放在实体层而不是某个 feature 里：填表页（`SlotMediaPicker`）、模板封装编辑器、
 * 提示词里的引用块都要显示"这条素材叫什么"，各写一份就会出现三种缩略图尺寸。
 */
export function AssetChip({
  assetId,
  onRemove,
  className,
}: {
  assetId: string;
  onRemove?: () => void;
  className?: string;
}) {
  const detail = useLibraryDetail(assetId);
  return (
    <span
      className={[
        'flex items-center gap-1.5 rounded-lg border border-line-soft bg-panel px-1.5 py-1',
        className ?? '',
      ].join(' ')}
    >
      <CoverImage
        src={detail.data?.coverUrl ?? undefined}
        seed={assetId}
        className="h-7 w-7 shrink-0 rounded"
      />
      <span className="max-w-[150px] truncate text-[11px] text-ink-soft">
        {detail.data?.name ?? assetId.slice(0, 8)}
      </span>
      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          className="text-ink-mute transition-colors hover:text-danger"
          title="移除"
        >
          <X size={11} />
        </button>
      )}
    </span>
  );
}
