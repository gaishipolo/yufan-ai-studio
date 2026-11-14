import { useQuery } from '@tanstack/react-query';
import { X } from 'lucide-react';
import { libraryApi } from '@/entities/library/api/library.api';
import { CoverImage } from '@/shared/ui';

/**
 * 封装 chip：首图 + 名称 + 「N 张」。
 *
 * 封装是「一组成员」，在填表页和封装编辑器里都只显示成一块（成员顺序、用途由
 * 资产库决定），所以两处共用同一个渲染 —— 否则同一个封装会有两种外观。
 */
export function PackChip({packId, onRemove}: {packId: string; onRemove?: () => void}) {
  const pack = useQuery({
    queryKey: ['library', 'pack', packId],
    queryFn: () => libraryApi.packDetail(packId),
    enabled: !!packId,
    staleTime: 30_000,
  });
  const items = pack.data?.packItems ?? [];
  return (
    <span className="flex items-center gap-1.5 rounded-lg border border-line-soft bg-panel px-1.5 py-1">
      <CoverImage
        src={items[0]?.coverUrl ?? undefined}
        seed={packId}
        className="h-7 w-7 shrink-0 rounded"
      />
      <span className="max-w-[180px] truncate text-[11px] text-ink-soft">
        {pack.data?.name ?? packId.slice(0, 8)}
      </span>
      <span className="text-[11px] text-ink-mute">{items.length} 张</span>
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
