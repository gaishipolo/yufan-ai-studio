import { Button, Chip } from '@/shared/ui';
import { useArchiveAssets } from '@/entities/asset/api/archive.queries';
import { ARCHIVE_LABELS } from '@/entities/asset/model/archive.types';
import { batchCounts, batchProgress, hasUploading, type BatchCounts } from './model/upload-batch';
import { useUploadBatchStore } from './model/use-upload-batch';
import { ArchiveBatchItem } from './ArchiveBatchItem';

function summaryText(counts: BatchCounts): string {
  const parts = [`成功 ${counts.success}`, `失败 ${counts.failed}`, `待核对 ${counts.unknown}`, `跳过 ${counts.skipped}`];
  if (counts.queued > 0) parts.push(`未处理 ${counts.queued}`);
  return parts.join(' · ');
}

/**
 * 上传批次面板。
 *
 * 这里不放"全部重试"：不同文件的失败原因不同，「未知」项更是禁止重传。
 * 每一条自成一行，用户逐个决定，这才是他能对账的粒度。
 */
export function ArchiveUploadPanel() {
  const batch = useUploadBatchStore(state => state.batch);
  const dismiss = useUploadBatchStore(state => state.dismiss);
  /* 只在批次暂停于「结果待核对」时才需要列表来做候选匹配，其余时候不发请求。 */
  const needsList = !!batch?.items.some(item => item.status === 'unknown');
  const assets = useArchiveAssets(batch?.category ?? 'source', {enabled: needsList});
  if (!batch) return null;
  const counts = batchCounts(batch);
  const progress = batchProgress(batch);
  const uploading = hasUploading(batch);
  const running = batch.phase === 'running';
  const current = batch.items.find(item => item.status === 'uploading');
  return <section aria-label="上传批次" className="space-y-2 rounded-xl border border-primary/20 bg-panel p-3">
    <div className="flex flex-wrap items-center gap-2">
      <p className="text-xs font-semibold text-ink">本批次归档到：{ARCHIVE_LABELS[batch.category]}</p>
      <Chip tone={running ? 'primary' : batch.phase === 'paused' ? 'warn' : 'default'}>
        {running ? '进行中' : batch.phase === 'paused' ? '已暂停' : '已结束'}
      </Chip>
      <span className="ml-auto text-[11px] text-ink-mute" aria-live="polite">{summaryText(counts)}</span>
    </div>
    <p className="text-[11px] text-ink-mute" aria-live="polite">
      {running
        ? `正在归档第 ${Math.min(progress.index, progress.total)} / ${progress.total} 个${current ? `：${current.file.name}` : ''}`
        : batch.phase === 'paused'
          ? '已暂停：先处理下面这一条，或关闭批次后重新开始。暂停期间分类与其它提交入口保持锁定。'
          : '批次已结束，分类与上传入口已恢复。'}
    </p>
    <ul className="space-y-2">
      {batch.items.map(item => <ArchiveBatchItem key={item.key} item={item} startedAt={batch.startedAt}
        assets={assets.data?.items} onCheck={() => assets.refetch().then(() => undefined)} />)}
    </ul>
    {/* 暂停时也要能关闭：否则分类被锁、面板又没有出口，用户只能刷新页面。
         store 会拒绝在请求在途时清空批次。 */}
    {!uploading && <div className="flex flex-wrap items-center gap-2">
      <Button size="sm" variant="secondary" onClick={dismiss}>关闭批次</Button>
      <span className="text-[11px] text-ink-mute">关闭只清除这份清单，已归档的文件仍在列表里。</span>
    </div>}
  </section>;
}
