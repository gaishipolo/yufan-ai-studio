import { X } from 'lucide-react';
import { useLibraryDetail } from '@/entities/library/api/library.queries';
import { CoverImage } from '@/shared/ui';

/**
 * 素材 chip：缩略图 + 名字（+ 可选移除）。
 *
 * 每个 id 一条详情查询 —— React Query 按 id 缓存，切模板或换标签时不会重复拉。
 * 放在独立文件里而不是 `SpecLibrary.tsx` 内部：素材选择块（`SlotMediaPicker`）
 * 也要用它显示"已选"，而选择块本身被 `SpecLibrary` 引用，留在同一个文件里就成环。
 */
export function AssetChip({
  assetId,
  onRemove,
}: {
  assetId: string;
  onRemove?: () => void;
}) {
  const detail = useLibraryDetail(assetId);
  return (
    <span className="flex items-center gap-1.5 rounded-lg border border-line-soft bg-panel px-1.5 py-1">
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
