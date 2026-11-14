import { memo } from 'react';
import { Images } from 'lucide-react';
import type { ImageFeedItem } from '@/shared/api/schema';
import { cn } from '@/shared/lib/cn';
import { formatDateTime } from '@/shared/lib/format';
import { Chip, CoverImage } from '@/shared/ui';

export interface ImageTaskCardProps {
  item: ImageFeedItem;
  onOpen: (id: string) => void;
}

const STATUS_LABEL: Record<string, string> = {
  queued: '排队中',
  running: '生成中',
  succeeded: '已完成',
  failed: '生成失败',
};

const STATUS_STYLE: Record<string, string> = {
  queued: 'bg-[rgba(43,124,255,.92)]',
  running: 'bg-[rgba(43,124,255,.92)]',
  succeeded: 'bg-[rgba(23,192,122,.9)]',
  failed: 'bg-[rgba(242,84,75,.92)]',
};

/** 任务中心里的生图卡片：与视频卡同栅格，但没有勾选框（生图不支持批量删除/收藏）。 */
export const ImageTaskCard = memo(function ImageTaskCard({ item, onOpen }: ImageTaskCardProps) {
  const inFlight = item.status === 'queued' || item.status === 'running';
  const cover = item.coverUrl || item.outputUrl || '';

  return (
    <article
      className={cn(
        'group relative cursor-pointer overflow-hidden rounded-card border border-line-soft bg-panel transition-all duration-200',
        'hover:-translate-y-0.5 hover:border-line-strong hover:shadow-card',
      )}
    >
      <button
        type="button"
        aria-label={`查看生图：${item.title}`}
        onClick={() => onOpen(item.id)}
        className="absolute inset-0 z-[1] rounded-card focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
      />

      <div className="relative">
        <CoverImage
          src={cover}
          seed={item.id}
          alt={item.title}
          className="aspect-[4/5] w-full"
          imgClassName="transition-transform duration-300 group-hover:scale-[1.04]"
        />
        <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(180deg,rgba(8,20,26,.2)_0%,rgba(8,20,26,0)_32%,rgba(8,20,26,.46)_100%)]" />

        <span className="absolute left-2 top-2 inline-flex items-center gap-1 rounded-pill bg-[rgba(10,22,28,.5)] px-1.5 py-px text-[10px] text-white backdrop-blur-[6px]">
          <Images size={9} aria-hidden />
          生图
        </span>
        {(item.status !== 'succeeded' || item.group === 'attention') && (
          <span
            className={cn(
              'absolute bottom-2 left-2 rounded-pill px-1.5 py-px text-[10px] font-semibold text-white backdrop-blur-[6px]',
              STATUS_STYLE[item.status] ?? 'bg-[rgba(10,22,28,.58)]',
            )}
          >
            {item.group === 'attention'
              ? '成图待恢复'
              : (STATUS_LABEL[item.status] ?? item.status)}
          </span>
        )}
        <span className="absolute bottom-2 right-2 rounded-[5px] bg-[rgba(10,22,28,.5)] px-1.5 py-px text-[10px] tabular-nums text-white">
          {/* 后端 size 是 `1024x1024`：x 换成乘号，和模板库参数区同一写法 */}
          {(item.size ?? '').replace('x', '×')} · ×{item.count ?? 1}
        </span>
      </div>

      <div className="relative px-2.5 pb-2.5 pt-2">
        <p className="line-clamp-2 min-h-9 break-all text-xs leading-[18px] text-ink">{item.title}</p>
        {inFlight ? (
          <>
            <div className="mt-2 h-[3px] overflow-hidden rounded-full bg-line-soft">
              <i
                className="block h-full rounded-full bg-[linear-gradient(90deg,#4f9dff,#12c4c8)] transition-[width] duration-500"
                style={{ width: `${item.progress ?? 0}%` }}
              />
            </div>
            <div className="mt-2 flex items-center justify-between gap-2 text-[10.5px] text-ink-mute">
              <Chip tone="primary" className="max-w-[70%] truncate">
                {item.model}
              </Chip>
              <span className="shrink-0 tabular-nums">{item.progress ?? 0}%</span>
            </div>
          </>
        ) : (
          /* 与视频卡同一套节奏：模型在左、时间在右，规格已经在封面角标上 */
          <div className="mt-2 flex items-center justify-between gap-2 text-[10.5px] text-ink-mute">
            <Chip tone="primary" className="max-w-[62%] truncate">
              {item.model}
            </Chip>
            <span className="shrink-0 truncate">{formatDateTime(item.createdAt)}</span>
          </div>
        )}
      </div>
    </article>
  );
});
