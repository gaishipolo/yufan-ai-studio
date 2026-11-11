import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Layers } from 'lucide-react';
import { Button, Chip, EmptyState, Modal, type ChipTone } from '@/shared/ui';
import { ErrorState } from '@/shared/ui/ErrorState';
import { Skeleton } from '@/shared/ui/EmptyState';
import { useCan } from '@/shared/access/use-access';
import { formatDateTime } from '@/shared/lib/format';
import { useReplacementCatalog } from '@/entities/template/api/replacement.queries';
import { REPLACEMENT_STATUS, REVIEW_LABELS } from '@/entities/template/model/replacement.labels';
import { ArchivedMedia } from '@/entities/asset/ui/ArchivedMedia';

function statusTone(status: string): ChipTone {
  if (status === 'success') return 'ok';
  if (status === 'failed' || status === 'timeout' || status === 'submission_unknown') return 'danger';
  return 'default';
}

function reviewTone(status: string): ChipTone {
  if (status === 'accepted') return 'ok';
  if (status === 'rejected') return 'danger';
  return 'warn';
}

/**
 * 生成结果归档。
 *
 * 三个独立事实不能混：有没有权限读模板、读取成功与否、成片有没有通过人工验收。
 * 以前只取 `data`，三者任一不成立都显示「暂无已下载的生成结果」。
 */
export function ArchivedResults() {
  const canRead = useCan('template:read');
  const catalog = useReplacementCatalog({enabled: canRead});
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  /* 刷新按钮只反映手动触发的那一次：任务在跑时目录每 5 秒自动轮询，
     直接绑 isFetching 会让按钮一直转圈，看起来像卡住了。 */
  async function refresh() {
    setRefreshing(true);
    try { await catalog.refetch(); } finally { setRefreshing(false); }
  }
  const results = catalog.data?.runs.filter(run => run.outputUrl) ?? [];
  const selected = results.find(run => run.id === selectedId);
  if (!canRead) return <section className="space-y-2 border-t border-line pt-5">
    <h2 className="text-sm font-semibold text-ink">生成结果归档</h2>
    <p role="status" className="text-xs text-ink-mute">
      当前账号没有查看生成结果的权限（需要 template:read），这里不会读取模板目录。素材区操作不受影响。
    </p>
  </section>;
  return <section className="space-y-3 border-t border-line pt-5">
    <div className="flex items-center justify-between gap-2">
      <h2 className="text-sm font-semibold text-ink">
        生成结果归档{catalog.data ? ` · ${results.length} 份` : ''}
      </h2>
      <Button size="sm" loading={refreshing} onClick={() => {void refresh();}}>刷新</Button>
    </div>
    <p className="text-xs text-ink-mute">成片保存在各自任务的 outputs 中；「成片已保存」不等于人工验收通过。</p>
    {catalog.error && <ErrorState error={catalog.error} onRetry={() => {void refresh();}} retrying={refreshing}
      title={catalog.data ? '结果更新失败，以下为上次加载的记录' : '生成结果加载失败'} />}
    {catalog.isPending && !catalog.data
      ? <div role="status" aria-label="正在读取生成结果" className="grid gap-3 md:grid-cols-3">
        <Skeleton className="h-24" /><Skeleton className="h-24" /><Skeleton className="h-24" />
      </div>
      : results.length > 0
        ? <div className="grid gap-3 md:grid-cols-3">{results.map(run => {
          const review = run.reviewStatus ?? 'pending';
          return <button key={run.id} type="button" onClick={() => setSelectedId(run.id)}
            className="rounded-xl border border-line bg-panel p-4 text-left transition hover:border-primary">
            <p className="text-sm font-semibold text-ink">{run.target}</p>
            <p className="mt-1 text-xs text-ink-mute">{formatDateTime(run.createdAt)}</p>
            <p className="mt-2 flex flex-wrap items-center gap-1.5">
              <Chip tone={statusTone(run.status)}>{REPLACEMENT_STATUS[run.status] ?? run.status}</Chip>
              <Chip tone={reviewTone(review)}>人工验收：{REVIEW_LABELS[review] ?? review}</Chip>
            </p>
            {run.taskId && <p className="mt-1.5 truncate text-[11px] text-ink-mute" title={run.taskId}>任务 {run.taskId}</p>}
          </button>;
        })}</div>
        : !catalog.error && <EmptyState icon={<Layers size={30} />} title="暂无已归档的生成结果"
          description="完成一次替换生成后，成片会出现在这里。" className="py-8" />}
    <Modal open={!!selected} onClose={() => setSelectedId(null)} title={selected?.target}>
      {selected?.outputUrl && <div className="space-y-3 p-4">
        <ArchivedMedia url={selected.outputUrl} type="video" name={`${selected.target}-${selected.id}.mp4`} />
        <p className="text-xs text-ink-mute">归档位置：{selected.archivePath ?? '目录已不在（可能已被清理）'}</p>
        <Link to="/templates" className="inline-block text-xs text-primary underline">前往模板库查看任务 →</Link>
      </div>}
    </Modal>
  </section>;
}
