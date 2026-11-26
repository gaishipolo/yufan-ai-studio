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
  /**
   * 上游排队情况的一句话（如「0 积分队列 3/10」），由任务中心按上游 task_id 查好后传进来。
   *
   * 卡片本身**不去查**：一页 48 张卡各发一次请求，等于把一个批量接口用成 48 个；
   * 而且「排队第几」属于生成链路的细节，卡片只负责显示。
   */
  queueHint?: string | null;
  /** 上游实时进度覆盖值（进行中优先显示来自 LibTV 的最新百分比）。 */
  liveProgress?: number;
}

/* 封面角标的底色：
   - 在飞（排队中 / 生成中）= 深色底 + 一颗青点。青绿是这个系统的
     「在飞」信号色（token 注释里写死了：只有真的在跑的东西才用它），
     以前这里用的是蓝 —— 于是蓝同时兼任「行动、选中、在飞」三件事，
     一屏全是蓝的，状态反而看不见了。
   - 失败 = 红（唯一保留的实心色，因为失败必须一眼看到）。
   - 完成 = 不摆角标（画面上没东西可失败，就别占位置）。 */
const STATUS_STYLE: Record<VideoTask['status'], string> = {
  queued: 'bg-[rgba(10,22,28,.62)]',
  running: 'bg-[rgba(10,22,28,.62)]',
  failed: 'bg-[rgba(176,57,28,.92)]',
  completed: 'bg-[rgba(10,22,28,.62)]',
};

export const TaskCard = memo(function TaskCard({
  task,
  selected,
  onToggleSelect,
  onOpen,
  queueHint,
  liveProgress,
}: TaskCardProps) {
  const inFlight = task.status === 'running' || task.status === 'queued';
  const erase = task.subtitleErase ?? null;
  const eraseRunning = erase?.status === 'queued' || erase?.status === 'running';
  const shownProgress = liveProgress ?? task.progress;

  return (
    <article
      className={cn(
        'group relative cursor-pointer overflow-hidden rounded-card border bg-panel transition-colors duration-200',
        selected ? 'border-primary shadow-[0_0_0_2px_rgba(23,21,15,.20)]' : 'border-line-soft hover:border-line-strong',
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
          /* 封面加一圈内描边：浅色封面直接贴在白卡片上，边界会糊掉 */
          className="aspect-[4/5] w-full ring-1 ring-inset ring-[rgba(12,24,30,.10)]"
          imgClassName="transition-transform duration-300 group-hover:scale-[1.04]"
        />

        {/* 上下渐隐遮罩，保证角标可读；比上一版轻一档，浅色封面不再发灰 */}
        <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(180deg,rgba(8,20,26,.2)_0%,rgba(8,20,26,0)_32%,rgba(8,20,26,.46)_100%)]" />

        <span
          className={cn(
            'absolute left-2 top-2 inline-flex items-center gap-1 rounded-xs px-1.5 py-px font-mono text-micro font-semibold tracking-[.06em] text-white backdrop-blur-[6px]',
            STATUS_STYLE[task.status],
            task.status === 'completed' && 'hidden',
          )}
        >
          {inFlight && (
            <i className="h-1.5 w-1.5 animate-pulse rounded-full bg-brand motion-reduce:animate-none" aria-hidden />
          )}
          {TASK_STATUS_LABEL[task.status]}
        </span>

        {task.status !== 'failed' && (
          <span className="absolute bottom-2 left-2 inline-flex items-center gap-1 rounded-xs border border-white/20 bg-[rgba(10,22,28,.5)] px-1.5 py-px text-micro text-white opacity-0 backdrop-blur-[6px] transition-opacity group-hover:opacity-100 focus-within:opacity-100">
            <Play size={9} className="fill-current" />
            预览
          </span>
        )}

        {!inFlight && task.status !== 'failed' && (
          /* 画幅与时长合并成一个角标：它们都是"这条片子是什么规格"，
             分成两个 pill 会把封面右下角塞满。失败的任务没有片子，不摆这张标签。 */
          <span className="absolute bottom-2 right-2 rounded-xs bg-[rgba(10,22,28,.5)] px-1.5 py-px font-mono text-micro tabular-nums text-white">
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
        <p className="line-clamp-2 min-h-9 break-all text-xs leading-[18px] text-ink">{task.title}</p>

        {inFlight ? (
          <>
            <div className="mt-2 h-[3px] overflow-hidden rounded-xs bg-line-soft">
              <i
                /* 进度条也是「在飞」——所以它是青绿渐变，不是主色蓝。
                   一屏里蓝留给选中与行动，青留给正在发生的。 */
                /* 进度条是「在飞」—— 全站唯一的彩色，朱红。 */
                className="block h-full rounded-xs bg-[linear-gradient(90deg,#c4552f,#a8341a)] transition-[width] duration-500"
                style={{ width: `${shownProgress}%` }}
              />
            </div>
            <div className="mt-2 flex items-center justify-between gap-2 text-micro text-ink-mute">
              {/* 模型名是**内容**，不是状态：中性 chip。
                  一屏四张卡就有四块蓝底，真正该被看见的「生成中」反而被淹了。 */}
              <Chip className="max-w-[70%] truncate">{task.model}</Chip>
              <span className="shrink-0 font-mono tabular-nums">{shownProgress}%</span>
            </div>
            {/* 「48% 卡了半小时」和「排第 3 位」看起来完全一样，差别只有上游知道。
                有排队信息时就补一行，没有就不占位。 */}
            {queueHint ? (
              <p className="mt-1 truncate text-micro text-warn-deep" title={queueHint}>
                {queueHint}
              </p>
            ) : null}
          </>
        ) : (
          /* 规格已经搬到封面角标上，这一行只留「哪个模型」和「什么时候」 */
          <div className="mt-2 flex items-center justify-between gap-2 text-micro text-ink-mute">
            <Chip className="max-w-[52%] truncate">{task.model}</Chip>
            {erase?.status === 'completed' ? (
              <Chip tone="brand" className="shrink-0">
                去字幕
              </Chip>
            ) : null}
            {/* 时间是数据：等宽 + tabular，跟下面那列的数字对得上 */}
            <span className="ml-auto shrink-0 truncate font-mono tabular-nums">
              {formatDateTime(task.createdAt)}
            </span>
          </div>
        )}
        {eraseRunning ? (
          <p className="mt-1 truncate text-micro text-brand-deep">
            去字幕 {erase?.progress ?? 0}%
          </p>
        ) : null}
        {erase?.status === 'failed' ? (
          <p className="mt-1 truncate text-micro text-danger" title={erase.errorMessage ?? undefined}>
            去字幕失败：{erase.errorMessage || '请打开详情重试'}
          </p>
        ) : null}
      </div>
    </article>
  );
});
