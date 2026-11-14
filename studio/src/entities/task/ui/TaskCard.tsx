import { memo } from 'react';
import { Check, Play } from 'lucide-react';
import { cn } from '@/shared/lib/cn';
import { formatDateTime, formatDuration } from '@/shared/lib/format';
import { CoverImage, Chip } from '@/shared/ui';
import { TASK_STATUS_LABEL, type VideoTask } from '../model/types';

export interface TaskCardProps {
  task: VideoTask;
  selected: boolean;
  onToggleSelect: (id: string) => void;
  onOpen: (id: string) => void;
}

const STATUS_STYLE: Record<VideoTask['status'], string> = {
  queued: 'bg-[rgba(43,124,255,.92)]',
  running: 'bg-[rgba(43,124,255,.92)]',
  failed: 'bg-[rgba(242,84,75,.92)]',
  completed: 'bg-[rgba(23,192,122,.9)]',
};

export const TaskCard = memo(function TaskCard({
  task,
  selected,
  onToggleSelect,
  onOpen,
}: TaskCardProps) {
  const inFlight = task.status === 'running' || task.status === 'queued';

  return (
    <article
      className={cn(
        'group relative cursor-pointer overflow-hidden rounded-card border bg-panel transition-all duration-200',
        'hover:-translate-y-0.5 hover:shadow-card',
        selected ? 'border-primary shadow-[0_0_0_2px_rgba(43,124,255,.18)]' : 'border-line-soft hover:border-line-strong',
      )}
    >
      {/* 整卡可点击区：用真实 button 铺满，键盘与读屏都能到（CODE_STYLE 3.7） */}
      <button
        type="button"
        aria-label={`查看任务：${task.title}`}
        onClick={() => onOpen(task.id)}
        className="absolute inset-0 z-[1] rounded-card focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
      />

      <div className="relative">
        <CoverImage
          src={task.coverUrl}
          seed={task.id}
          alt={task.title}
          className="aspect-[4/5] w-full"
          imgClassName="transition-transform duration-300 group-hover:scale-[1.04]"
        />

        {/* 上下渐隐遮罩，保证角标可读；比上一版轻一档，浅色封面不再发灰 */}
        <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(180deg,rgba(8,20,26,.2)_0%,rgba(8,20,26,0)_32%,rgba(8,20,26,.46)_100%)]" />

        <span
          className={cn(
            'absolute left-2 top-2 rounded-pill px-1.5 py-px text-[10px] font-semibold text-white backdrop-blur-[6px]',
            STATUS_STYLE[task.status],
            task.status === 'completed' && 'hidden',
          )}
        >
          {TASK_STATUS_LABEL[task.status]}
        </span>

        {task.status !== 'failed' && (
          <span className="absolute bottom-2 left-2 inline-flex items-center gap-1 rounded-pill border border-white/20 bg-[rgba(10,22,28,.5)] px-1.5 py-px text-[10px] text-white backdrop-blur-[6px]">
            <Play size={9} className="fill-current" />
            预览
          </span>
        )}

        {!inFlight && task.status !== 'failed' && (
          /* 画幅与时长合并成一个角标：它们都是"这条片子是什么规格"，
             分成两个 pill 会把封面右下角塞满。失败的任务没有片子，不摆这张标签。 */
          <span className="absolute bottom-2 right-2 rounded-[5px] bg-[rgba(10,22,28,.5)] px-1.5 py-px text-[10px] tabular-nums text-white">
            {task.aspectRatio} · {formatDuration(task.durationSec)}
          </span>
        )}

        {/* 勾选是独立动作：浮在整卡按钮之上，阻止冒泡开卡 */}
        <button
          type="button"
          aria-label={selected ? `取消选择：${task.title}` : `选择任务：${task.title}`}
          aria-pressed={selected}
          onClick={(e) => {
            e.stopPropagation();
            onToggleSelect(task.id);
          }}
          className={cn(
            'absolute right-2 top-2 z-[2] grid h-[17px] w-[17px] place-items-center rounded-[5px] border-[1.5px] transition-opacity',
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
        <p className="line-clamp-2 min-h-9 break-all text-xs leading-[18px] text-ink">{task.title}</p>

        {inFlight ? (
          <>
            <div className="mt-2 h-[3px] overflow-hidden rounded-full bg-line-soft">
              <i
                className="block h-full rounded-full bg-[linear-gradient(90deg,#4f9dff,#12c4c8)] transition-[width] duration-500"
                style={{ width: `${task.progress}%` }}
              />
            </div>
            <div className="mt-2 flex items-center justify-between gap-2 text-[10.5px] text-ink-mute">
              <Chip tone="primary" className="max-w-[70%] truncate">
                {task.model}
              </Chip>
              <span className="shrink-0 tabular-nums">{task.progress}%</span>
            </div>
          </>
        ) : (
          /* 规格已经搬到封面角标上，这一行只留「哪个模型」和「什么时候」 */
          <div className="mt-2 flex items-center justify-between gap-2 text-[10.5px] text-ink-mute">
            <Chip tone="primary" className="max-w-[62%] truncate">
              {task.model}
            </Chip>
            <span className="shrink-0 truncate">{formatDateTime(task.createdAt)}</span>
          </div>
        )}
      </div>
    </article>
  );
});
