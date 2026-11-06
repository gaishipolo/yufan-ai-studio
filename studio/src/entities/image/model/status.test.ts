import { describe, expect, it } from 'vitest';
import type { Paginated } from '@/shared/api/types';
import type { ImageJob } from './types';
import {
  IMAGE_POLL_INTERVAL,
  clampProgress,
  hasPendingImages,
  imagePollInterval,
  imageSrcOf,
  imageStatusChipOf,
  imageStatusOf,
  isPendingImage,
} from './status';

function job(overrides: Partial<ImageJob> = {}): ImageJob {
  return {
    id: 'img-1',
    name: '一只白猫',
    prompt: 'a white cat',
    size: '1024x1024',
    createdAt: '2026-11-06T01:00:00Z',
    ...overrides,
  };
}

function page(items: ImageJob[]): Paginated<ImageJob> {
  return { items, total: items.length, page: 1, pageSize: 24 };
}

describe('异步状态判定', () => {
  it('没有 status 的老记录按已完成处理，不会让轮询空转', () => {
    expect(imageStatusOf(job())).toBe('succeeded');
    expect(isPendingImage(job())).toBe(false);
  });

  it('只有 queued / running 算未完成', () => {
    expect(isPendingImage(job({ status: 'queued' }))).toBe(true);
    expect(isPendingImage(job({ status: 'running' }))).toBe(true);
    expect(isPendingImage(job({ status: 'succeeded' }))).toBe(false);
    expect(isPendingImage(job({ status: 'failed' }))).toBe(false);
  });

  it('一页里只要有一条未完成就继续轮询，全部落定立刻停', () => {
    expect(hasPendingImages(undefined)).toBe(false);
    expect(hasPendingImages(page([]))).toBe(false);
    expect(hasPendingImages(page([job({ status: 'succeeded' }), job({ status: 'failed' })]))).toBe(
      false,
    );
    expect(
      hasPendingImages(page([job({ status: 'succeeded' }), job({ status: 'running' })])),
    ).toBe(true);
  });

  it('轮询间隔：有未完成返回 3 秒，否则 false', () => {
    expect(imagePollInterval(page([job({ status: 'queued' })]))).toBe(IMAGE_POLL_INTERVAL);
    expect(imagePollInterval(page([job({ status: 'succeeded' })]))).toBe(false);
    expect(imagePollInterval(undefined)).toBe(false);
  });
});

describe('成图地址与状态展示', () => {
  it('outputUrl 优先于 coverUrl（后者可能是会过期的供应商签名地址）', () => {
    expect(
      imageSrcOf(job({ coverUrl: 'https://vendor/signed', outputUrl: '/static/images/done.png' })),
    ).toBe('/static/images/done.png');
  });

  it('outputUrl 为 null 时回落 coverUrl，排队中两者皆空则用占位图', () => {
    expect(imageSrcOf(job({ coverUrl: 'https://vendor/signed', outputUrl: null }))).toBe(
      'https://vendor/signed',
    );
    expect(imageSrcOf(job({ coverUrl: '', outputUrl: null }))).toBe('');
    expect(imageSrcOf(job())).toBe('');
  });

  it('进度收敛在 0–100，非法值不会撑破进度条', () => {
    expect(clampProgress(undefined)).toBe(0);
    expect(clampProgress(Number.NaN)).toBe(0);
    expect(clampProgress(-3)).toBe(0);
    expect(clampProgress(150)).toBe(100);
    expect(clampProgress(42.6)).toBe(43);
  });

  it('四种状态各自有可读角标，已完成不显示', () => {
    expect(imageStatusChipOf(job({ status: 'queued' }))).toEqual({
      tone: 'default',
      text: '排队中',
      progress: null,
    });
    expect(imageStatusChipOf(job({ status: 'running', progress: 42 }))).toEqual({
      tone: 'primary',
      text: '生成中 42%',
      progress: 42,
    });
    expect(imageStatusChipOf(job({ status: 'failed' }))?.text).toBe('生成失败');
    expect(imageStatusChipOf(job({ status: 'succeeded', progress: 100 }))).toBeNull();
  });

  it('后端新增未知状态时不静默当作成功', () => {
    expect(imageStatusChipOf(job({ status: 'cancelled' }))).toEqual({
      tone: 'warn',
      text: 'cancelled',
      progress: null,
    });
  });
});
