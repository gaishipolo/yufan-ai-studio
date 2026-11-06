import { useState } from 'react';
import { Button, Chip, type ChipTone } from '@/shared/ui';
import { formatBytes, formatDateTime } from '@/shared/lib/format';
import type { ArchivedAsset } from '@/entities/asset/model/archive.types';
import { archivedCandidates, UPLOAD_STATUS_TEXT, type BatchItem, type UploadItemStatus } from './model/upload-batch';
import { useUploadBatchStore } from './model/use-upload-batch';

const TONES: Record<UploadItemStatus, ChipTone> = {
  queued: 'default', uploading: 'primary', success: 'ok', failed: 'danger', unknown: 'warn', skipped: 'default',
};

interface ArchiveBatchItemProps {
  item: BatchItem;
  startedAt: number;
  /** 最新列表；只有「结果待核对」的项会用到。 */
  assets?: ArchivedAsset[];
  onCheck: () => Promise<void>;
}

/**
 * 批次中的一条文件。
 *
 * 「结果待核对」不提供重传按钮：请求可能已经写进归档盘，重传会得到两份同名文件。
 * 只给证据（列表里的候选）和两个明确的决定。
 */
export function ArchiveBatchItem({item, startedAt, assets, onCheck}: ArchiveBatchItemProps) {
  const retry = useUploadBatchStore(state => state.retry);
  const requeue = useUploadBatchStore(state => state.requeue);
  const skip = useUploadBatchStore(state => state.skip);
  const confirm = useUploadBatchStore(state => state.confirm);
  const [checking, setChecking] = useState(false);
  const [busy, setBusy] = useState(false);
  const candidates = checking && item.status === 'unknown'
    ? archivedCandidates(item.file, startedAt, assets ?? [])
    : [];
  /* 刷新失败不在这里额外提示：列表自己的错误态会说明原因，
     这里再弹一句只会重复。 */
  async function check() {
    setBusy(true);
    setChecking(true);
    try { await onCheck(); } catch { /* 已由列表错误态承担 */ }
    finally { setBusy(false); }
  }
  return <li className="rounded-lg border border-line bg-panel-soft p-2.5">
    <div className="flex flex-wrap items-center gap-2">
      <span className="min-w-0 flex-1 truncate text-xs text-ink" title={item.file.name}>{item.file.name}</span>
      <span className="text-[11px] text-ink-mute">{formatBytes(item.file.size)}</span>
      <Chip tone={TONES[item.status]}>{UPLOAD_STATUS_TEXT[item.status]}</Chip>
    </div>
    {item.error && <p className="mt-1 break-all text-[11px] text-danger">{item.error}</p>}
    {item.status === 'failed' && <div className="mt-2 flex gap-2">
      <Button size="sm" onClick={() => retry(item.key)}>重试此项</Button>
      <Button size="sm" variant="secondary" onClick={() => skip(item.key)}>跳过</Button>
    </div>}
    {item.status === 'unknown' && <div className="mt-2 space-y-2">
      <div className="flex flex-wrap gap-2">
        <Button size="sm" loading={busy} onClick={() => {void check();}}>刷新并核对</Button>
        <Button size="sm" variant="secondary" onClick={() => skip(item.key)}>跳过</Button>
      </div>
      {checking && <div className="space-y-1.5 rounded-lg border border-line bg-panel p-2">
        {candidates.length > 0 ? <>
          <p className="text-[11px] text-ink-soft">列表里找到同名同大小的记录。确认是这次上传的文件吗？</p>
          {candidates.slice(0, 3).map(asset => <div key={asset.id} className="flex flex-wrap items-center gap-2">
            <span className="min-w-0 flex-1 truncate text-[11px] text-ink-mute">
              {formatDateTime(asset.createdAt)} · {asset.name}
            </span>
            <Button size="sm" onClick={() => confirm(item.key, asset.id)}>确认是这条</Button>
          </div>)}
          <Button size="sm" variant="secondary" onClick={() => requeue(item.key)}>不是，重新上传</Button>
        </> : <div className="flex flex-wrap items-center gap-2">
          <span className="min-w-0 flex-1 text-[11px] text-ink-mute">
            没找到匹配记录。服务端索引可能有延迟，不据此判断"没保存"。
          </span>
          <Button size="sm" variant="secondary" onClick={() => requeue(item.key)}>重新上传</Button>
        </div>}
      </div>}
    </div>}
  </li>;
}
