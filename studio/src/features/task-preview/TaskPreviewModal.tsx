import { Bookmark, Download, RotateCcw } from 'lucide-react';
import { Button, Chip, CoverImage, Modal, Skeleton } from '@/shared/ui';
import { toast } from '@/shared/lib/toast';
import { downloadUrl } from '@/shared/lib/download';
import { formatDateTime, formatDuration } from '@/shared/lib/format';
import { useRetryTask, useTaskDetail, useToggleFavorite } from '@/entities/task/api/task.queries';
import { TASK_STATUS_LABEL } from '@/entities/task/model/types';

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

export function TaskPreviewModal({ taskId, outputUrl, onClose }: TaskPreviewModalProps) {
  const { data: task, isLoading } = useTaskDetail(taskId);
  const retry = useRetryTask();
  const favorite = useToggleFavorite();

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
          <CoverImage
            src={task.coverUrl}
            seed={task.id}
            alt={task.title}
            className="mx-auto aspect-[4/4.8] w-full max-w-[240px] rounded-[10px] md:max-w-none"
          />

          <div className="min-w-0">
            <dl className="grid grid-cols-[76px_minmax(0,1fr)] gap-x-2.5 gap-y-2 text-xs">
              <dt className="text-ink-mute">任务 ID</dt>
              <dd className="break-all text-ink">{task.id}</dd>

              <dt className="text-ink-mute">状态</dt>
              <dd>
                <Chip tone={STATUS_TONE[task.status]}>
                  {TASK_STATUS_LABEL[task.status]}
                  {task.status === 'running' ? ` · ${task.progress}%` : ''}
                </Chip>
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
              <dd className="text-ink">{task.prompt}</dd>
            </dl>

            {task.errorMessage && (
              <p className="mt-3 rounded-lg border border-[#fad7d4] bg-[#feeceb] px-3 py-2 text-[11.5px] leading-relaxed text-[#d94036]">
                {task.errorMessage}
              </p>
            )}

            <div className="mt-4 flex flex-wrap gap-2">
              <Button
                size="sm"
                variant="primary"
                icon={<Download size={13} />}
                disabled={!task.downloadable || !outputUrl}
                onClick={() =>
                  outputUrl
                    ? downloadUrl(outputUrl, `${task.title}.mp4`)
                    : toast('成片地址暂不可用，请稍后刷新', 'error')
                }
              >
                下载成片
              </Button>
              <Button
                size="sm"
                icon={<RotateCcw size={13} />}
                loading={retry.isPending}
                onClick={() =>
                  retry.mutate(task.id, {
                    onSuccess: () => toast('已提交重新生成', 'success'),
                    onError: (e) => toast(e.message, 'error'),
                  })
                }
              >
                重新生成
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
            </div>

          </div>
        </div>
      )}
    </Modal>
  );
}
