import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Download, RotateCcw } from 'lucide-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Button, Chip, CoverImage, Modal } from '@/shared/ui';
import { toast } from '@/shared/lib/toast';
import { downloadUrl } from '@/shared/lib/download';
import { formatDateTime, generatedFileName } from '@/shared/lib/format';
import { imageApi } from '@/entities/image/api/image.api';
import { PromptRefs } from '@/entities/library/ui/PromptRefs';
import { useSessionStore } from '@/shared/access/session';
import { taskKeys } from '@/entities/task/api/task.queries';
import type { TaskFeedItem } from '@/entities/task/model/feed.types';

export interface ImagePreviewModalProps {
  item: TaskFeedItem | null;
  onClose: () => void;
}

const STATUS_LABEL: Record<string, string> = {
  queued: '排队中',
  running: '生成中',
  succeeded: '已完成',
  failed: '生成失败',
};

function suffixOf(url: string): string {
  const path = url.split('?')[0] ?? '';
  const match = /\.(png|jpe?g|webp|gif)$/i.exec(path);
  return match ? match[0].toLowerCase() : '.png';
}

/** 生图在任务中心没有独立详情接口：弹窗直接用 feed 里的投影，不额外发请求。 */
export function ImagePreviewModal({ item, onClose }: ImagePreviewModalProps) {
  const qc = useQueryClient();
  const [needConfirm, setNeedConfirm] = useState(false);
  const image = item?.image;
  const me = useSessionStore(state => state.subject.name);

  useEffect(() => setNeedConfirm(false), [item?.id]);

  const retry = useMutation({
    mutationFn: (confirmCharged: boolean) => imageApi.retry(item?.id ?? '', confirmCharged),
    onSuccess: () => {
      toast('已重新排队', 'success');
      void qc.invalidateQueries({ queryKey: taskKeys.feeds() });
      setNeedConfirm(false);
      onClose();
    },
    onError: (error: unknown) => {
      const message = error instanceof Error ? error.message : '重试失败';
      // 后端对「可能已扣费」的记录返回 409；把二次确认直接放在按钮上，
      // 不让用户去猜 query 参数。
      if (/扣费|核对|confirmCharged/.test(message)) setNeedConfirm(true);
      toast(message, 'error');
    },
  });

  const outputUrl = image?.outputUrl ?? null;
  const inAttention = image?.group === 'attention';

  return (
    <Modal open={Boolean(item)} onClose={onClose} title={image?.title ?? '生图详情'} width={780}>
      {item && image && (
        <div className="grid gap-5 p-[18px] md:grid-cols-[280px_minmax(0,1fr)]">
          <CoverImage
            src={outputUrl || image.coverUrl || ''}
            seed={item.id}
            alt={image.title}
            className="mx-auto aspect-square w-full max-w-[280px] rounded-[10px] object-cover"
          />

          <div className="min-w-0">
            <dl className="grid grid-cols-[76px_minmax(0,1fr)] gap-x-2.5 gap-y-2 text-xs">
              <dt className="text-ink-mute">任务 ID</dt>
              <dd className="break-all text-ink">{image.id}</dd>

              <dt className="text-ink-mute">状态</dt>
              <dd>
                <Chip tone={image.status === 'failed' ? 'danger' : image.status === 'succeeded' ? 'ok' : 'primary'}>
                  {inAttention ? '成图待恢复' : (STATUS_LABEL[image.status] ?? image.status)}
                  {image.status === 'running' ? ` · ${image.progress ?? 0}%` : ''}
                </Chip>
              </dd>

              <dt className="text-ink-mute">生成模型</dt>
              <dd className="text-ink">{image.model || '—'}</dd>

              <dt className="text-ink-mute">尺寸 / 张数</dt>
              <dd className="text-ink">{image.size} · {image.count ?? 1} 张</dd>

              <dt className="text-ink-mute">创建时间</dt>
              <dd className="text-ink">{formatDateTime(image.createdAt)}</dd>

              <dt className="text-ink-mute">画面描述</dt>
              <dd className="break-words text-ink">
                {/* 与任务详情同一套：文案给正文、参考图给缩略块，解析不到就留占位符 */}
                {image.prompt ? (
                  <PromptRefs
                    value={image.prompt}
                    textIds={image.textLibraryIds}
                    referenceIds={image.referenceLibraryIds}
                  />
                ) : (
                  '—'
                )}
              </dd>
            </dl>

            {image.errorMessage && (
              <p className="mt-3 rounded-lg border border-[#fad7d4] bg-[#feeceb] px-3 py-2 text-[11.5px] leading-relaxed text-[#d94036]">
                {image.errorMessage}
              </p>
            )}
            {inAttention && (
              <p className="mt-3 text-[11.5px] leading-relaxed text-ink-mute">
                这次生成已标记为成功，但成图地址不可用，无法下载。
              </p>
            )}

            <div className="mt-4 flex flex-wrap gap-2">
              <Button
                size="sm"
                variant="primary"
                icon={<Download size={13} />}
                disabled={!outputUrl}
                onClick={() =>
                  outputUrl &&
                  downloadUrl(
                    outputUrl,
                    generatedFileName(me, image.createdAt, suffixOf(outputUrl)),
                  )
                }
              >
                下载成图
              </Button>
              {image.status === 'failed' && (
                <Button
                  size="sm"
                  icon={<RotateCcw size={13} />}
                  loading={retry.isPending}
                  onClick={() => retry.mutate(needConfirm)}
                >
                  {needConfirm ? '已核对，仍要重试' : '重新生成'}
                </Button>
              )}
              {image.detailUrl && (
                <Link to={image.detailUrl} className="self-center text-xs text-primary hover:underline">
                  去生图历史 →
                </Link>
              )}
            </div>
          </div>
        </div>
      )}
    </Modal>
  );
}
