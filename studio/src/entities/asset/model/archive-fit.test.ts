import { describe, expect, it } from 'vitest';
import type { ArchivedAsset } from './archive.types';
import { replacementFitness } from './archive-fit';

function source(overrides: Partial<ArchivedAsset> = {}): ArchivedAsset {
  return {
    id: 'a1',
    name: '原片.mp4',
    category: 'source',
    type: 'video',
    mimeType: 'video/mp4',
    sizeBytes: 1024,
    path: 'library/source/2026/11/a1.mp4',
    sha256: 'x',
    createdAt: '2026-11-06T00:00:00Z',
    duration: 8,
    hasAudio: true,
    url: '/api/v1/assets/a1/file',
    ...overrides,
  };
}

describe('替换适用性', () => {
  it('带音轨且 1–15 秒的原片可用于替换', () => {
    expect(replacementFitness(source())).toEqual({ok: true});
  });

  it('缺音轨时说明原因，而不是只说不可用', () => {
    expect(replacementFitness(source({hasAudio: false}))).toEqual({ok: false, reason: '缺少音轨'});
  });

  it('时长越界判为不适用', () => {
    expect(replacementFitness(source({duration: 20}))).toEqual({ok: false, reason: '时长不在 1–15 秒'});
    expect(replacementFitness(source({duration: 0.5}))).toEqual({ok: false, reason: '时长不在 1–15 秒'});
  });

  it('没读到时长时说的是未知，不是超出范围', () => {
    expect(replacementFitness(source({duration: undefined}))).toEqual({ok: false, reason: '时长未知，需重新探测'});
  });

  it('音轨信息缺失与确实没有音轨分开表述', () => {
    expect(replacementFitness(source({hasAudio: undefined}))).toEqual({ok: false, reason: '音轨信息缺失，需重新探测'});
    expect(replacementFitness(source({hasAudio: false}))).toEqual({ok: false, reason: '缺少音轨'});
  });

  it('非原片分类与非视频类型分别给出原因', () => {
    expect(replacementFitness(source({category: 'character'}))).toEqual({ok: false, reason: '仅原片参与替换'});
    expect(replacementFitness(source({type: 'image'}))).toEqual({ok: false, reason: '替换需要视频原片'});
  });

  it('边界值 1 秒与 15 秒算适用', () => {
    expect(replacementFitness(source({duration: 1}))).toEqual({ok: true});
    expect(replacementFitness(source({duration: 15}))).toEqual({ok: true});
  });
});
