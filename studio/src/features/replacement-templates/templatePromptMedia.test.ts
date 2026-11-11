import { describe, expect, it } from 'vitest';
import type { ReplacementPack, VideoTemplateOut } from '@/shared/api/schema';
import { templatePromptMedia } from './templatePromptMedia';

describe('模板提示词的预览绑定', () => {
  it('三个人物逐包对应原编号，原片可在第4位，A的第二张正面图不占B的位置', () => {
    const template = {defaultKind: 'character', rules: {referenceBindings: {
      '{{Mixed 1}}': 'front', '{{Mixed 2}}': 'front', '{{Mixed 3}}': 'front', '{{Mixed 4}}': 'source_video',
    }}, mediaOrder: []} as unknown as VideoTemplateOut;
    const packs = [
      {id: 'a', name: 'A', referenceIds: ['a1', 'a2', 'as'], bindings: [{role: 'front'}, {role: 'front'}, {role: 'side'}]},
      {id: 'b', name: 'B', referenceIds: ['b1'], bindings: [{role: 'front'}]},
      {id: 'c', name: 'C', referenceIds: ['c1'], bindings: [{role: 'front'}]},
    ].map(item => ({...item, kind: 'character', version: 1})) as ReplacementPack[];
    const source = {id: 'video', name: '原片', url: '/video'};
    const result = templatePromptMedia(template, source, packs[0], packs);
    expect(result.map(item => item.id)).toEqual(['a1', 'b1', 'c1', 'video', 'a2', 'as']);
    expect(result.map(item => item.token)).toEqual(['{{Mixed 1}}', '{{Mixed 2}}', '{{Mixed 3}}', '{{Mixed 4}}', '{{Mixed 5}}', '{{Mixed 6}}']);
    const partial = templatePromptMedia(template, source, packs[0], [packs[0]]);
    expect(partial[1].url).toBeUndefined();
    expect(partial[2].url).toBeUndefined();
    expect(partial[3].id).toBe('video');
    expect(partial.slice(4).map(item => item.id)).toEqual(['a2', 'as']);
  });
  it('换包后按用途取图，额外图片仍列在末尾，不能拿封装时的旧图片冒充', () => {
    const template = {sourceVideoAssetId: 'video', sourceVideoName: '原片', mediaOrder: [
      {token: '{{Mixed 1}}', type: 'video'},
      {token: '{{Mixed 2}}', type: 'image', assetId: 'old-view', role: 'multiview'},
      {token: '{{Mixed 3}}', type: 'image', assetId: 'old-front', role: 'front'},
    ]} as VideoTemplateOut;
    const pack = {id: 'p', kind: 'product', name: '新包', version: 1,
      referenceIds: ['new-front', 'new-view', 'extra'],
      bindings: [{role: 'front'}, {role: 'multiview'}, {role: 'detail'}],
      previewUrls: ['/front', '/view', '/detail'],
    } as ReplacementPack;
    const result = templatePromptMedia(template, {id: 'video', name: '原片', url: '/video'}, pack);
    expect(result.map(item => item.id)).toEqual(['video', 'new-view', 'new-front', 'extra']);
    expect(result[1].url).toBe('/view');
    expect(result[2].token).toBe('{{Mixed 3}}');
    expect(result.every(item => !item.id.startsWith('old'))).toBe(true);
  });

  it('包不可用时保留已知编号，图像标记不可用而非读取私人原件', () => {
    const template = {sourceVideoAssetId: 'video', sourceVideoName: '原片', mediaOrder: [
      {token: '{{Mixed 2}}', type: 'image', assetId: 'private', name: '旧参考图'},
    ]} as VideoTemplateOut;
    const result = templatePromptMedia(template, undefined, undefined);
    expect(result.find(item => item.id === 'private')?.url).toBeUndefined();
  });
});
