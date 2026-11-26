import { memo } from 'react';
import { Check, Images } from 'lucide-react';
import type { ImageFeedItem } from '@/shared/api/schema';
import { cn } from '@/shared/lib/cn';
import { formatDateTime } from '@/shared/lib/format';
import { Chip, CoverImage } from '@/shared/ui';

export interface ImageTaskCardProps {
  item: ImageFeedItem;
  selected: boolean;
  onToggleSelect: (id: string) => void;
  onOpen: (id: string) => void;
}

const STATUS_LABEL: Record<string, string> = {
  queued: '排队中',
  running: '生成中',
  succeeded: '已完成',
  failed: '生成失败',
};

/* 角标底色与视频卡同一套语言：
   在飞 = 深色底 + 青点（青绿是「在飞」信号色），失败 = 红，
   其余不摆实心色 —— 一屏卡片里只有「正在发生」和「出问题」值得抢眼。 */
const STATUS_STYLE: Record<string, string> = {
  queued: 'bg-[rgba(10,22,28,.62)]',
  running: 'bg-[rgba(10,22,28,.62)]',
  succeeded: 'bg-[rgba(10,22,28,.62)]',
  failed: 'bg-[rgba(176,57,28,.92)]',
};

/** 任务中心里的生图卡片：与视频卡同栅格、同样可勾选（批量删除 / 下载都支持）。 */
export const ImageTaskCard = memo(function ImageTaskCard({
  item,
  selected,
  onToggleSelect,
  onOpen,
}: ImageTaskCardProps) {
  const inFlight = item.status === 'queued' || item.status === 'running';
  const cover = item.coverUrl || item.outputUrl || '';

  return (
    <article
      className={cn(
        'group relative cursor-pointer overflow-hidden rounded-card border bg-panel transition-colors duration-200',
        selected
          ? 'border-primary shadow-[0_0_0_2px_rgba(23,21,15,.20)]'
          : 'border-line-soft hover:border-line-strong',
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
          /* 封面加一圈内描边：浅色封面直接贴在白卡片上，边界会糊掉 */
          className="aspect-[4/5] w-full ring-1 ring-inset ring-[rgba(12,24,30,.10)]"
          imgClassName="transition-transform duration-300 group-hover:scale-[1.04]"
        />
        <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(180deg,rgba(8,20,26,.2)_0%,rgba(8,20,26,0)_32%,rgba(8,20,26,.46)_100%)]" />

        <span className="absolute left-2 top-2 inline-flex items-center gap-1 rounded-pill bg-[rgba(10,22,28,.5)] px-1.5 py-px text-micro text-white backdrop-blur-[6px]">
          <Images size={9} aria-hidden />
          生图
        </span>
        {(item.status !== 'succeeded' || item.group === 'attention') && (
          <span
            className={cn(
              'absolute bottom-2 left-2 inline-flex items-center gap-1 rounded-xs px-1.5 py-px font-mono text-micro font-semibold tracking-[.06em] text-white backdrop-blur-[6px]',
              STATUS_STYLE[item.status] ?? 'bg-[rgba(10,22,28,.58)]',
            )}
          >
            {inFlight && (
              <i className="h-1.5 w-1.5 animate-pulse rounded-full bg-brand motion-reduce:animate-none" aria-hidden />
            )}
            {item.group === 'attention'
              ? '成图待恢复'
              : (STATUS_LABEL[item.status] ?? item.status)}
          </span>
        )}
        <span className="absolute bottom-2 right-2 rounded-xs bg-[rgba(10,22,28,.5)] px-1.5 py-px font-mono text-micro tabular-nums text-white">
          {/* 后端 size 是 `1024x1024`：x 换成乘号，和模板库参数区同一写法 */}
          {(item.size ?? '').replace('x', '×')} · ×{item.count ?? 1}
        </span>

        {/* 勾选与视频卡同一位置/样式：两类卡混在一屏里，操作习惯必须一致 */}
        <button
          type="button"
          aria-label={selected ? `取消选择：${item.title}` : `选择生图：${item.title}`}
          aria-pressed={selected}
          onClick={event => {
            event.stopPropagation();
            onToggleSelect(item.id);
          }}
          className={cn(
            'absolute right-2 top-2 z-[2] grid h-[17px] w-[17px] place-items-center rounded-md border-[1.5px] transition-opacity',
            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50',
            selected
              ? 'border-primary bg-primary text-white opacity-100'
              : 'border-white/85 bg-[rgba(10,22,28,.25)] text-white opacity-0 group-hover:opacity-100',
          )}
        >
          {selected && <Check size={11} strokeWidth={3} />}
        </button>
      </div>

      <div className="relative px-2.5 pb-2.5 pt-2">
        <p className="line-clamp-2 min-h-9 break-all text-xs leading-[18px] text-ink">{item.title}</p>
        {inFlight ? (
          <>
            <div className="mt-2 h-[3px] overflow-hidden rounded-xs bg-line-soft">
              <i
                /* 进度条是「在飞」——青绿渐变，不是主色蓝 */
                /* 进度条是「在飞」—— 全站唯一的彩色，朱红。 */
                className="block h-full rounded-xs bg-[linear-gradient(90deg,#c4552f,#a8341a)] transition-[width] duration-500"
                style={{ width: `${item.progress ?? 0}%` }}
              />
            </div>
            <div className="mt-2 flex items-center justify-between gap-2 text-micro text-ink-mute">
              {/* 模型名是内容不是状态：中性 chip，把颜色让给状态 */}
              <Chip className="max-w-[70%] truncate">{item.model}</Chip>
              <span className="shrink-0 font-mono tabular-nums">{item.progress ?? 0}%</span>
            </div>
          </>
        ) : (
          /* 与视频卡同一套节奏：模型在左、时间在右，规格已经在封面角标上 */
          <div className="mt-2 flex items-center justify-between gap-2 text-micro text-ink-mute">
            <Chip className="max-w-[62%] truncate">{item.model}</Chip>
            {/* 时间是数据：等宽 + tabular */}
            <span className="shrink-0 truncate font-mono tabular-nums">
              {formatDateTime(item.createdAt)}
            </span>
          </div>
        )}
      </div>
    </article>
  );
});
