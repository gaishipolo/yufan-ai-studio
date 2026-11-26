import { useState } from 'react';
import { Bookmark, Download, RotateCcw, Wand2 } from 'lucide-react';
import { isConfirmChargedRequired } from '@/shared/api/types';
import { Button, Chip, CoverImage, Modal, Notice, Skeleton } from '@/shared/ui';
import { toast } from '@/shared/lib/toast';
import { downloadUrl } from '@/shared/lib/download';
import { formatDateTime, formatDuration, generatedFileName } from '@/shared/lib/format';
import { useCan } from '@/shared/access/use-access';
import { useLibtvQueue } from '@/entities/libtv/api/libtv.queries';
import {
  isLibtvModelId,
  isQueued,
  liveTaskProgress,
  upstreamTaskId,
} from '@/entities/libtv/model/types';
import {
  useDesubtitle,
  useRetryTask,
  useTaskDetail,
  useToggleFavorite,
} from '@/entities/task/api/task.queries';
import { TASK_STATUS_LABEL, isTaskInFlight } from '@/entities/task/model/types';
import { PromptField } from './PromptField';

export interface TaskPreviewModalProps {
  taskId: string | null;
  /** 成片地址由 feed 提供（后端已按当前存储驱动现签），详情接口不返回它 */
  outputUrl?: string | null;
  onClose: () => void;
}

const STATUS_TONE = {
  queued: 'primary',
  running: 'primary',
  completed: 'ok',
  failed: 'danger',
} as const;

type PlaybackVersion = 'original' | 'erased';
const versionKey = (id: string) => `task-playback-version:${id}`;
function savedVersion(id: string | null): PlaybackVersion {
  try {
    return id && localStorage.getItem(versionKey(id)) === 'original' ? 'original' : 'erased';
  } catch {
    return 'erased';
  }
}

export function TaskPreviewModal({ taskId, outputUrl, onClose }: TaskPreviewModalProps) {
  const { data: task, isLoading } = useTaskDetail(taskId);
  const retry = useRetryTask();
  const eraseRetry = useRetryTask();
  const favorite = useToggleFavorite();
  const desubtitle = useDesubtitle();
  // 默认看去字幕版：这条链路存在的理由就是那版；原片留一个按钮随时能切回去。
  const [selection, setSelection] = useState<{ id: string; version: PlaybackVersion } | null>(null);
  const [confirmRetryId, setConfirmRetryId] = useState<string | null>(null);
  const [confirmEraseId, setConfirmEraseId] = useState<string | null>(null);
  const version = selection?.id === taskId ? selection.version : savedVersion(taskId);
  const erase = task?.subtitleErase ?? null;
  const erasedReady = erase?.status === 'completed' && Boolean(erase.outputUrl);
  const playingUrl = version === 'erased' && erasedReady ? erase?.outputUrl : outputUrl;
  const playingErased = version === 'erased' && erasedReady;
  const canReadQueue = useCan('task:read');
  const upstreamId =
    task && isTaskInFlight(task) && isLibtvModelId(task.modelId)
      ? upstreamTaskId(task.externalId)
      : null;
  const { data: queueData } = useLibtvQueue(
    upstreamId ? [upstreamId] : [],
    Boolean(upstreamId) && canReadQueue,
  );
  const liveItem = queueData?.items.find((item) => item.taskId === upstreamId) ?? null;
  const shownProgress = task ? liveTaskProgress(task.progress, liveItem) : 0;

  return (
    <Modal open={Boolean(taskId)} onClose={onClose} title={task?.title ?? '任务详情'} width={780}>
      {isLoading || !task ? (
        <div className="grid gap-5 p-[18px] md:grid-cols-[250px_minmax(0,1fr)]">
          <Skeleton className="aspect-[4/4.8] w-full" />
          <div className="space-y-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-3.5 w-full" />
            ))}
          </div>
        </div>
      ) : (
        <div className="grid gap-5 p-[18px] md:grid-cols-[250px_minmax(0,1fr)]">
          {/* 成片能播就播：以前这里只放一张封面图，用户点开「预览」看到的是静止画面，
              正文里也没有任何播放入口 —— 等于看不见生成结果。 */}
          {task.status === 'completed' && playingUrl ? (
            <div className="mx-auto w-full max-w-[240px] md:max-w-none">
              {erasedReady ? (
                <div className="mb-2 flex items-center gap-1.5" role="group" aria-label="播放版本">
                  {(
                    [
                      ['erased', '去字幕版'],
                      ['original', '原片'],
                    ] as const
                  ).map(([value, label]) => (
                    <button
                      key={value}
                      type="button"
                      aria-pressed={version === value}
                      onClick={() => {
                        setSelection({ id: task.id, version: value });
                        try { localStorage.setItem(versionKey(task.id), value); } catch { /* 当前弹窗仍可切换 */ }
                      }}
                      className={
                        version === value
                          ? 'rounded-pill bg-primary px-2 py-0.5 text-micro font-medium text-white'
                          : 'rounded-pill border border-line-soft px-2 py-0.5 text-micro text-ink-mute hover:border-line-strong'
                      }
                    >
                      {label}
                    </button>
                  ))}
                </div>
              ) : null}
              <video
                key={playingErased ? 'erased' : 'original'}
                src={playingUrl}
                poster={task.coverUrl || undefined}
                controls
                playsInline
                preload="metadata"
                aria-label={`成片：${task.title}`}
                className="max-h-[420px] w-full rounded-xl bg-black object-contain"
              >
                {/* 生成成片没有字幕轨，但 a11y 规则要求 video 里必须有 track；
                    与 `AssetPreview` 同一写法（空轨不改变播放行为）。 */}
                <track kind="captions" />
              </video>
            </div>
          ) : (
            <CoverImage
              src={task.coverUrl}
              seed={task.id}
              alt={task.title}
              className="mx-auto aspect-[4/4.8] w-full max-w-[240px] rounded-xl md:max-w-none"
            />
          )}

          <div className="min-w-0">
            <dl className="grid grid-cols-[76px_minmax(0,1fr)] gap-x-2.5 gap-y-2 text-xs">
              <dt className="text-ink-mute">任务 ID</dt>
              <dd className="break-all text-ink">{task.id}</dd>

              <dt className="text-ink-mute">状态</dt>
              <dd className="flex flex-wrap items-center gap-2">
                <Chip tone={STATUS_TONE[task.status]}>
                  {TASK_STATUS_LABEL[task.status]}
                  {isTaskInFlight(task) ? ` · ${shownProgress}%` : ''}
                </Chip>
                {liveItem && isQueued(liveItem) ? (
                  <span className="text-micro text-warn-deep">{liveItem.describe}</span>
                ) : null}
              </dd>

              <dt className="text-ink-mute">生成模型</dt>
              <dd className="text-ink">{task.model}</dd>

              <dt className="text-ink-mute">画面比例</dt>
              <dd className="text-ink">{task.aspectRatio}</dd>

              <dt className="text-ink-mute">时长</dt>
              <dd className="text-ink">{formatDuration(task.durationSec)}</dd>

              <dt className="text-ink-mute">创建时间</dt>
              <dd className="text-ink">{formatDateTime(task.createdAt)}</dd>

              <dt className="text-ink-mute">画面描述</dt>
              <dd className="min-w-0 text-ink">
                {/* 占位符换成真的东西：`{{Text N}}` → 正文、`{{Mixed N}}` → 缩略图。
                    解析不到（历史任务缺快照）就原样留着，比如实吞掉好。
                    正文限高自己滚 —— 不然长提示词会把下面的按钮顶出视口。 */}
                <PromptField
                  value={task.prompt}
                  textIds={task.textLibraryIds}
                  referenceIds={task.referenceLibraryIds}
                />
              </dd>
            </dl>

            {task.errorMessage && (
              <Notice tone="error" className="mt-3">
                {task.errorMessage}
              </Notice>
            )}
            {erase?.status === 'failed' && erase.errorMessage && (
              <Notice tone="error" className="mt-3">去字幕失败：{erase.errorMessage}</Notice>
            )}

            <div className="mt-4 flex flex-wrap gap-2">
              <Button
                size="sm"
                variant="primary"
                icon={<Download size={13} />}
                disabled={!task.downloadable || !playingUrl}
                onClick={() => {
                  if (!playingUrl) {
                    toast('成片地址暂不可用，请稍后刷新', 'error');
                    return;
                  }
                  const extMatch = /\.(mp4|mov|webm)(?:$|[?#])/i.exec(playingUrl);
                  const ext = extMatch ? `.${extMatch[1].toLowerCase()}` : '.mp4';
                  const base = generatedFileName(task.authorName ?? '', task.createdAt, ext);
                  const name = playingErased ? base.replace(/(\.[^.]+)$/, '-去字幕$1') : base;
                  void downloadUrl(playingUrl, name);
                }}
              >
                下载成片
              </Button>

              <Button
                size="sm"
                icon={<RotateCcw size={13} />}
                loading={retry.isPending}
                onClick={() =>
                  retry.mutate(
                    { id: task.id, confirmCharged: confirmRetryId === task.id },
                    {
                      onSuccess: () => {
                        setConfirmRetryId(null);
                        toast('已提交重新生成', 'success');
                      },
                      onError: (e) => {
                        if (isConfirmChargedRequired(e)) {
                          setConfirmRetryId(task.id);
                        }
                        toast(e.message, 'error');
                      },
                    },
                  )
                }
              >
                {confirmRetryId === task.id ? '已核对，仍要重新生成' : '重新生成'}
              </Button>
              <Button
                size="sm"
                icon={<Bookmark size={13} />}
                loading={favorite.isPending}
                onClick={() =>
                  favorite.mutate(task.id, {
                    onSuccess: (t) => toast(t.favorited ? '已收藏' : '已取消收藏', 'success'),
                    onError: (e) => toast(e.message, 'error'),
                  })
                }
              >
                {task.favorited ? '取消收藏' : '收藏'}
              </Button>
              <Button
                size="sm"
                icon={<Wand2 size={13} />}
                loading={
                  desubtitle.isPending || eraseRetry.isPending ||
                  erase?.status === 'queued' ||
                  erase?.status === 'running'
                }
                disabled={task.status !== 'completed' || erase?.status === 'completed'}
                onClick={() => {
                  if (erase?.status === 'failed') {
                    eraseRetry.mutate({ id: erase.id, confirmCharged: confirmEraseId === erase.id }, {
                      onSuccess: () => {
                        setConfirmEraseId(null);
                        toast('已重新提交去字幕', 'success');
                      },
                      onError: (e) => {
                        if (isConfirmChargedRequired(e)) setConfirmEraseId(erase.id);
                        toast(e.message, 'error');
                      },
                    });
                    return;
                  }
                  desubtitle.mutate(task.id, {
                    onSuccess: () => toast('已提交去字幕', 'success'),
                    onError: (e) => toast(e.message, 'error'),
                  });
                }}
              >
                {erase?.status === 'completed'
                  ? '已去字幕'
                  : erase && erase.status !== 'failed'
                    ? `去字幕 ${erase.progress}%`
                    : erase?.status === 'failed'
                      ? confirmEraseId === erase.id ? '已核对，仍要重试去字幕' : '重试去字幕'
                      : '智能去字幕'}
              </Button>
            </div>

          </div>
        </div>
      )}
    </Modal>
  );
}
