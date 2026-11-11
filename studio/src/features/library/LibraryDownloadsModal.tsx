import { Download, Loader2 } from 'lucide-react';
import { Button, Chip, Modal } from '@/shared/ui';
import { formatBytes, formatDateTime } from '@/shared/lib/format';
import { toast } from '@/shared/lib/toast';
import { useDownloadJobs, useSaveDownload } from '@/entities/library/api/library.queries';

export interface LibraryDownloadsModalProps {
  open: boolean;
  onClose: () => void;
}

const STATUS_TEXT: Record<string, string> = {
  queued: '排队中',
  running: '打包中',
  succeeded: '已完成',
  failed: '失败',
  expired: '已过期',
};

/**
 * 打包任务列表。
 *
 * 有任务在 `queued/running` 时每 1.5 秒轮询一次，全部落定即停（见
 * `useDownloadJobs` 的 `refetchInterval`）——不做「永久轮询一个空列表」。
 */
export function LibraryDownloadsModal({ open, onClose }: LibraryDownloadsModalProps) {
  const query = useDownloadJobs(open);
  const save = useSaveDownload();
  const jobs = query.data ?? [];

  return (
    <Modal open={open} onClose={onClose} title="打包任务" width={560}>
      <div className="space-y-2 p-[18px]">
        {!jobs.length ? (
          <p className="rounded-lg bg-panel-soft px-3 py-8 text-center text-[13.5px] text-ink-mute">
            还没有打包任务。在列表里勾选若干资产后点「打包下载」。
          </p>
        ) : (
          <ul className="max-h-[420px] space-y-2 overflow-auto pr-1">
            {jobs.map((job) => {
              const running = job.status === 'queued' || job.status === 'running';
              const done = job.status === 'succeeded';
              return (
                <li key={job.id} className="rounded-lg border border-line-soft bg-panel p-2.5">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="font-mono text-[13px] text-ink-mute">{job.id.slice(0, 8)}</span>
                    <Chip tone={done ? 'ok' : job.status === 'failed' ? 'danger' : 'warn'}>
                      {STATUS_TEXT[job.status] ?? job.status}
                    </Chip>
                    <span className="text-[13.5px] text-ink-soft">
                      {job.assetCount ?? 0} 项
                      {job.sizeBytes ? ` · ${formatBytes(job.sizeBytes)}` : ''}
                    </span>
                    <span className="ml-auto text-[13px] text-ink-mute">
                      {formatDateTime(job.createdAt)}
                    </span>
                  </div>
                  {job.error && <p className="mt-1 text-[13px] text-danger">{job.error}</p>}
                  <div className="mt-1.5 flex items-center gap-1.5">
                    {running && <Loader2 size={13} className="animate-spin text-primary" />}
                    <Button
                      size="sm"
                      variant="secondary"
                      icon={<Download size={12} />}
                      disabled={!done || running}
                      loading={save.isPending}
                      onClick={() =>
                        save.mutate(
                          { id: job.id, filename: `library-${job.id.slice(0, 8)}.zip` },
                          {
                            onSuccess: () => toast('已开始下载'),
                            onError: (error) => toast(error.message, 'error'),
                          },
                        )
                      }
                    >
                      下载 zip
                    </Button>
                    {job.expiresAt && (
                      <span className="text-[13px] text-ink-mute">
                        有效至 {formatDateTime(job.expiresAt)}
                      </span>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}

        <div className="flex justify-end border-t border-line-soft pt-3">
          <Button size="sm" variant="ghost" onClick={onClose}>
            关闭
          </Button>
        </div>
      </div>
    </Modal>
  );
}
