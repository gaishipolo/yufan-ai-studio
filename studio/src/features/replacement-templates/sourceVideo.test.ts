import { describe, expect, it } from 'vitest';
import { SOURCE_VIDEO_ORIGINS } from '@/entities/library/model/types';
import { sourceVideoFitness, sourceVideoOfArchive, sourceVideoOfLibrary, type SourceVideo } from './sourceVideo';

function video(extra: Partial<SourceVideo> = {}): SourceVideo {
  return {id: 'v1', name: '原片.mp4', type: 'video', url: '/api/v1/library/assets/v1/file',
    previewUrl: null, durationSec: 5, width: 720, height: 1280, hasAudio: true, ...extra};
}

describe('原片视图', () => {
  it('资产库行按 v2 字段名映射：durationSec 而不是 duration', () => {
    expect(sourceVideoOfLibrary({id: 'v2', name: '库原片', url: '/file', previewUrl: '/thumb',
      durationSec: 3.5, width: 1080, height: 1920, hasAudio: false})).toEqual({
      id: 'v2', name: '库原片', type: 'video', url: '/file', previewUrl: '/thumb',
      durationSec: 3.5, width: 1080, height: 1920, hasAudio: false,
    });
  });

  it('旧库行按 duration 映射，缺值一律留 null 而不是 0', () => {
    const mapped = sourceVideoOfArchive({id: 'v1', name: '旧原片', category: 'source',
      type: 'video', url: '/old', mimeType: 'video/mp4', sizeBytes: 5, path: 'a.mp4',
      sha256: 'hash', createdAt: '2026-11-06T00:00:00Z'});
    expect(mapped.durationSec).toBeNull();
    expect(mapped.hasAudio).toBeNull();
    expect(mapped.previewUrl).toBeNull();
  });

  it('适配判定与后端门禁同口径：音轨、时长未知、1–15 秒', () => {
    expect(sourceVideoFitness(video())).toEqual({ok: true});
    expect(sourceVideoFitness(video({hasAudio: false}))).toEqual({ok: false, reason: '缺少音轨'});
    expect(sourceVideoFitness(video({hasAudio: null}))).toEqual({ok: false, reason: '音轨信息缺失，需重新探测'});
    expect(sourceVideoFitness(video({durationSec: null}))).toEqual({ok: false, reason: '时长未知，需重新探测'});
    expect(sourceVideoFitness(video({durationSec: 0.5}))).toEqual({ok: false, reason: '时长不在 1–15 秒'});
    expect(sourceVideoFitness(video({durationSec: 15}))).toEqual({ok: true});
  });

  it('原片的两个来源必须一起取', () => {
    expect(SOURCE_VIDEO_ORIGINS).toEqual(['template_source', 'imported']);
  });
});
