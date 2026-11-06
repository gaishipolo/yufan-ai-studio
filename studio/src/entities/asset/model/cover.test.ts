import { describe, expect, it } from 'vitest';
import { resolveCoverSource } from './cover';
import type { Asset } from './types';

function asset(coverUrl: string): Asset {
  return {
    id: 'A1',
    name: 'x.png',
    type: 'image',
    mimeType: 'image/png',
    sizeBytes: 1,
    width: 1,
    height: 1,
    duration: 0,
    hasAudio: false,
    category: null,
    source: 'upload',
    coverUrl,
    fileUrl: '/api/v1/assets/A1/file',
    refCount: 0,
    createdAt: '2026-11-06T01:00:00Z',
  };
}

describe('resolveCoverSource', () => {
  it('没有封面时不取任何地址（列表里每张卡都发一次请求只会刷一屏 404）', () => {
    expect(resolveCoverSource(asset(''))).toEqual({});
  });

  it('托管封面是站内相对路径：走带鉴权的 Blob', () => {
    expect(resolveCoverSource(asset('/api/v1/assets/A1/cover'))).toEqual({ blobId: 'A1' });
  });

  it('远端封面是绝对地址：直接交给 <img>，不再请求取封面端点', () => {
    expect(resolveCoverSource(asset('https://picsum.photos/seed/x/600/600'))).toEqual({
      directUrl: 'https://picsum.photos/seed/x/600/600',
    });
  });
});
