import { ApiError, errorHttpStatus } from '@/shared/api/types';
import type { ArchiveCategory, ArchivedAsset } from '@/entities/asset/model/archive.types';

/**
 * 上传批次的纯状态机。
 *
 * 页面以前只有一个 toast：「N 个文件已归档」或「上传失败」。两种说法都不够——
 * 5 个文件在第 3 个失败时，前 2 个已经写进归档盘，剩下 2 个根本没发出去，
 * 而用户看到的是一句无法归因的失败。这里把每一件的结局分别记下来。
 *
 * 关键区分：**明确失败**可以重发，**结果未知**不能。
 * 4xx 是服务端明确拒绝（类型不符、超限、越权），重发同一文件是安全的；
 * 408 / 断连 / 5xx 说明请求可能已经到了归档盘，只是回执没回来，
 * 直接重传会得到两份文件，而列表里两条同名记录无法用肉眼分辨。
 */

export type UploadItemStatus = 'queued' | 'uploading' | 'success' | 'failed' | 'unknown' | 'skipped';
export type BatchPhase = 'running' | 'paused' | 'done';

/** 每一条都必须有文字状态，不能只靠颜色区分。 */
export const UPLOAD_STATUS_TEXT: Record<UploadItemStatus, string> = {
  queued: '未处理',
  uploading: '正在上传',
  success: '已归档',
  failed: '归档失败 · 可重试',
  unknown: '结果待核对 · 禁止直接重传',
  skipped: '已跳过',
};

export interface BatchItem {
  /** `crypto.randomUUID()`。不能用文件名：同名多选是合法的，会让第二条覆盖第一条。 */
  key: string;
  file: File;
  status: UploadItemStatus;
  assetId?: string;
  error?: string;
}

export interface UploadBatch {
  id: string;
  /** 批次开始时的分类快照。中途 URL 变化不改写它，否则同一批文件会落到两个分类。 */
  category: ArchiveCategory;
  startedAt: number;
  items: BatchItem[];
  phase: BatchPhase;
}

export type BatchEvent =
  | {type: 'create'; id: string; category: ArchiveCategory; files: File[]; startedAt: number}
  | {type: 'begin'; key: string}
  | {type: 'succeed'; key: string; assetId: string}
  | {type: 'reject'; key: string; message: string; status: 'failed' | 'unknown'}
  | {type: 'retry'; key: string}
  | {type: 'requeue'; key: string}
  | {type: 'skip'; key: string}
  | {type: 'confirm'; key: string; assetId: string}
  | {type: 'dismiss'};

function nextPhase(items: BatchItem[]): BatchPhase {
  return items.some(item => item.status === 'queued') ? 'running' : 'done';
}

function patch(batch: UploadBatch, key: string, change: Partial<BatchItem>, phase?: BatchPhase): UploadBatch {
  if (!batch.items.some(item => item.key === key)) return batch;
  const items = batch.items.map(item => (item.key === key ? {...item, ...change} : item));
  return {...batch, items, phase: phase ?? nextPhase(items)};
}

export function batchReducer(batch: UploadBatch | null, event: BatchEvent): UploadBatch | null {
  if (event.type === 'dismiss') return null;
  if (event.type === 'create') {
    if (event.files.length === 0) return null;
    return {
      id: event.id,
      category: event.category,
      startedAt: event.startedAt,
      phase: 'running',
      items: event.files.map(file => ({key: crypto.randomUUID(), file, status: 'queued'})),
    };
  }
  if (!batch) return null;
  switch (event.type) {
    case 'begin':
      return patch(batch, event.key, {status: 'uploading', error: undefined}, 'running');
    case 'succeed':
      return patch(batch, event.key, {status: 'success', assetId: event.assetId, error: undefined});
    case 'reject':
      return patch(batch, event.key, {status: event.status, error: event.message}, 'paused');
    case 'retry':
      /* 只有明确失败可以原地重发。 */
      return batch.items.some(item => item.key === event.key && item.status === 'failed')
        ? patch(batch, event.key, {status: 'queued', error: undefined}, 'running')
        : batch;
    case 'requeue':
      /* 结果未知的项要重传，必须走这个事件：它是用户核对列表之后的显式决定。 */
      return batch.items.some(item => item.key === event.key && item.status === 'unknown')
        ? patch(batch, event.key, {status: 'queued', error: undefined}, 'running')
        : batch;
    case 'skip':
      return batch.items.some(item => item.key === event.key && (item.status === 'failed' || item.status === 'unknown'))
        ? patch(batch, event.key, {status: 'skipped'})
        : batch;
    case 'confirm':
      return batch.items.some(item => item.key === event.key && item.status === 'unknown')
        ? patch(batch, event.key, {status: 'success', assetId: event.assetId, error: undefined})
        : batch;
    default:
      return batch;
  }
}

/** 下一条待处理的文件；没有则由 runner 收尾。 */
export function nextQueued(batch: UploadBatch): BatchItem | undefined {
  return batch.items.find(item => item.status === 'queued');
}

/** 已处理的序号，用于「正在归档第 X / N 个」。不含 queued 之后的下标歧义。 */
export function batchProgress(batch: UploadBatch): {index: number; total: number} {
  const finished = batch.items.filter(item => item.status === 'success' || item.status === 'skipped').length;
  return {index: finished + 1, total: batch.items.length};
}

export interface BatchCounts {
  success: number; failed: number; unknown: number; skipped: number; queued: number; uploading: number;
}

export function batchCounts(batch: UploadBatch): BatchCounts {
  const counts: BatchCounts = {success: 0, failed: 0, unknown: 0, skipped: 0, queued: 0, uploading: 0};
  for (const item of batch.items) counts[item.status] += 1;
  return counts;
}

/** 有请求在途：上传入口显示转圈，而不是让按钮看起来可点。 */
export function hasUploading(batch: UploadBatch | null): boolean {
  return !!batch?.items.some(item => item.status === 'uploading');
}

/** 批次未结束时锁定分类与其它提交入口。 */
export function isBatchLocked(batch: UploadBatch | null): boolean {
  return !!batch && batch.phase !== 'done';
}

/**
 * 明确失败 vs 结果未知。
 *
 * 复用已有的 `ApiError.code`（`code // 100` 即 HTTP 状态），不新增错误类型：
 * 拦截器已把超时归一成 408、断网归一成 -1，后端业务错误带真实状态段。
 */
export function classifyUploadError(error: unknown): 'failed' | 'unknown' {
  if (!(error instanceof ApiError)) return 'unknown';
  const status = errorHttpStatus(error.code);
  return status >= 400 && status < 500 && status !== 408 ? 'failed' : 'unknown';
}

export function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : '请求失败';
}

/** 服务端时间与浏览器时钟可能有偏差，核对候选时给一点余量。 */
const CLOCK_SKEW_MS = 5 * 60 * 1000;

/**
 * 在最新列表里找「疑似已归档」的记录。
 *
 * 同名同大小只是线索，不是结论：两次上传同名文件完全合法。
 * 因此这里只返回候选，由用户在界面上确认，绝不自动改状态。
 */
export function archivedCandidates(file: File, startedAt: number, assets: ArchivedAsset[]): ArchivedAsset[] {
  const floor = startedAt - CLOCK_SKEW_MS;
  return assets.filter(asset => asset.name === file.name
    && asset.sizeBytes === file.size
    && new Date(asset.createdAt).getTime() >= floor);
}
