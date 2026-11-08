import { describe, expect, it } from 'vitest';
import { ApiError } from '@/shared/api/types';
import type { ArchivedAsset } from '@/entities/asset/model/archive.types';
import {
  archivedCandidates, batchCounts, batchReducer, classifyUploadError, type UploadBatch,
} from './upload-batch';

function file(name: string, size = 10): File {
  return new File([new Uint8Array(size)], name);
}

function created(names = ['a.mp4', 'b.mp4', 'c.mp4']): UploadBatch {
  const batch = batchReducer(null, {
    type: 'create', id: 'batch', category: 'source', files: names.map(name => file(name)), startedAt: 1_700_000_000_000,
  });
  if (!batch) throw new Error('批次未创建');
  return batch;
}

function statuses(batch: UploadBatch): string[] {
  return batch.items.map(item => item.status);
}

function keyAt(batch: UploadBatch, index: number): string {
  const item = batch.items[index];
  if (!item) throw new Error('下标越界');
  return item.key;
}

describe('批次状态机', () => {
  it('创建后全部排队，分类被快照下来', () => {
    const batch = created();
    expect(statuses(batch)).toEqual(['queued', 'queued', 'queued']);
    expect(batch.phase).toBe('running');
    expect(batch.category).toBe('source');
  });

  it('没有文件时不创建批次', () => {
    expect(batchReducer(null, {type: 'create', id: 'x', category: 'source', files: [], startedAt: 0})).toBeNull();
  });

  it('成功一项后仍未完成时继续运行', () => {
    const start = created();
    const batch = batchReducer(start, {type: 'succeed', key: keyAt(start, 0), assetId: 'a1'});
    expect(batch?.phase).toBe('running');
    expect(batch?.items[0]?.status).toBe('success');
    expect(batch?.items[0]?.assetId).toBe('a1');
  });

  it('最后一项成功后批次结束', () => {
    let batch = created(['a.mp4']);
    batch = batchReducer(batch, {type: 'succeed', key: keyAt(batch, 0), assetId: 'a1'}) ?? batch;
    expect(batch.phase).toBe('done');
  });

  it('明确失败暂停批次，后续项保持未处理而不是标成失败', () => {
    const start = created();
    const batch = batchReducer(start, {type: 'reject', key: keyAt(start, 1), message: '超出 300MB', status: 'failed'});
    expect(statuses(batch ?? start)).toEqual(['queued', 'failed', 'queued']);
    expect(batch?.phase).toBe('paused');
  });

  it('结果未知同样暂停，但状态与明确失败分开', () => {
    const start = created();
    const batch = batchReducer(start, {type: 'reject', key: keyAt(start, 0), message: '请求超时', status: 'unknown'});
    expect(batch?.items[0]?.status).toBe('unknown');
    expect(batch?.phase).toBe('paused');
  });

  it('明确失败可以重试，结果未知不能走重试事件', () => {
    const start = created();
    const failed = batchReducer(start, {type: 'reject', key: keyAt(start, 0), message: '类型不符', status: 'failed'});
    const retried = batchReducer(failed, {type: 'retry', key: keyAt(start, 0)});
    expect(retried?.items[0]?.status).toBe('queued');
    expect(retried?.phase).toBe('running');

    const unknown = batchReducer(start, {type: 'reject', key: keyAt(start, 0), message: '超时', status: 'unknown'});
    expect(batchReducer(unknown, {type: 'retry', key: keyAt(start, 0)})).toBe(unknown);
  });

  it('结果未知要重传必须走显式的 requeue', () => {
    const start = created();
    const unknown = batchReducer(start, {type: 'reject', key: keyAt(start, 0), message: '超时', status: 'unknown'});
    const requeued = batchReducer(unknown, {type: 'requeue', key: keyAt(start, 0)});
    expect(requeued?.items[0]?.status).toBe('queued');
    expect(requeued?.phase).toBe('running');
  });

  it('跳过失败项后继续处理剩余项', () => {
    const start = created();
    const failed = batchReducer(start, {type: 'reject', key: keyAt(start, 0), message: '坏文件', status: 'failed'});
    const skipped = batchReducer(failed, {type: 'skip', key: keyAt(start, 0)});
    expect(skipped?.items[0]?.status).toBe('skipped');
    expect(skipped?.phase).toBe('running');
  });

  it('用户核对后确认归档，未知项转为成功', () => {
    const start = created();
    const unknown = batchReducer(start, {type: 'reject', key: keyAt(start, 0), message: '超时', status: 'unknown'});
    const confirmed = batchReducer(unknown, {type: 'confirm', key: keyAt(start, 0), assetId: 'a9'});
    expect(confirmed?.items[0]?.status).toBe('success');
    expect(confirmed?.items[0]?.assetId).toBe('a9');
  });

  it('非法转换不改变状态，也不抛错', () => {
    const batch = created();
    expect(batchReducer(batch, {type: 'succeed', key: '不存在', assetId: 'x'})).toBe(batch);
    expect(batchReducer(batch, {type: 'skip', key: keyAt(batch, 0)})).toBe(batch);
    expect(batchReducer(batch, {type: 'confirm', key: keyAt(batch, 0), assetId: 'x'})).toBe(batch);
  });

  it('关闭批次后清空，且不改动已归档的事实', () => {
    expect(batchReducer(created(), {type: 'dismiss'})).toBeNull();
  });

  it('统计覆盖六种状态', () => {
    let batch = created(['a.mp4', 'b.mp4', 'c.mp4']);
    batch = batchReducer(batch, {type: 'succeed', key: keyAt(batch, 0), assetId: 'a1'}) ?? batch;
    batch = batchReducer(batch, {type: 'reject', key: keyAt(batch, 1), message: '超时', status: 'unknown'}) ?? batch;
    expect(batchCounts(batch)).toEqual({success: 1, failed: 0, unknown: 1, skipped: 0, queued: 1, uploading: 0});
  });
});

describe('上传错误分类', () => {
  it('4xx 是明确失败，可以重发同一文件', () => {
    expect(classifyUploadError(new ApiError('文件不能超过 300MB', 400))).toBe('failed');
    expect(classifyUploadError(new ApiError('类型不支持', 413))).toBe('failed');
  });

  it('超时、断连与 5xx 都是结果未知', () => {
    expect(classifyUploadError(new ApiError('请求超时，请稍后重试', 408))).toBe('unknown');
    expect(classifyUploadError(new ApiError('网络异常', -1))).toBe('unknown');
    expect(classifyUploadError(new ApiError('归档盘写入失败', 500))).toBe('unknown');
  });

  it('不是 ApiError 的一律按未知处理，不给重发入口', () => {
    expect(classifyUploadError(new Error('boom'))).toBe('unknown');
    expect(classifyUploadError('字符串')).toBe('unknown');
  });
});

describe('未知结果的候选匹配', () => {
  const startedAt = Date.parse('2026-11-06T02:00:00Z');
  function asset(overrides: Partial<ArchivedAsset> = {}): ArchivedAsset {
    return {
      id: 'a1', name: 'a.mp4', category: 'source', type: 'video', mimeType: 'video/mp4',
      sizeBytes: 10, path: 'library/source/2026/11/a1.mp4', sha256: 's', createdAt: '2026-11-06T02:00:30Z',
      url: '/api/v1/assets/a1/file', ...overrides,
    };
  }

  it('同名同大小且批次开始后创建才算候选', () => {
    expect(archivedCandidates(file('a.mp4'), startedAt, [asset()])).toHaveLength(1);
  });

  it('大小不同或早于批次开始的记录不算', () => {
    expect(archivedCandidates(file('a.mp4'), startedAt, [asset({sizeBytes: 99})])).toHaveLength(0);
    expect(archivedCandidates(file('a.mp4'), startedAt, [asset({createdAt: '2026-11-06T01:00:00Z'})])).toHaveLength(0);
  });

  it('浏览器时钟偏慢几分钟时仍能匹配到', () => {
    const skewed = startedAt + 3 * 60 * 1000;
    expect(archivedCandidates(file('a.mp4'), skewed, [asset()])).toHaveLength(1);
  });
});
