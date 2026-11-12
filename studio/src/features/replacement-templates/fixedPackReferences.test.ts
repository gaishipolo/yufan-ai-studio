import { describe, expect, it } from 'vitest';
import type { VideoTemplateOut } from '@/shared/api/schema';
import { fixedPackReferences, insertFixedMedia, removeFixedPackReference, withFixedReferences, type FixedPackReference } from './fixedPackReferences';

const refs: FixedPackReference[] = [
  {token: '{{Mixed 3}}', packId: 'a', packVersion: 2, assetId: 'a-image', name: '三视图.png', url: '/preview-a', type: 'image'},
  {token: '{{Mixed 4}}', packId: 'b', packVersion: 1, assetId: 'b-image', name: '场景.png', url: '/preview-b', type: 'image'},
];
describe('跨封装引用快照', () => {
  it('保存重开后仍解析相同图片与版本，辅助图片排在固定引用之后', () => {
    const template = {prompt: '{{Mixed 1}} {{Mixed 3}}', mediaOrder: [
      {token: '{{Mixed 1}}', type: 'video'}, {token: '{{Mixed 2}}', type: 'image'},
    ]} as VideoTemplateOut;
    const saved = withFixedReferences(template, refs);
    expect(fixedPackReferences(JSON.parse(JSON.stringify(saved)))).toEqual(refs);
    const media = insertFixedMedia([{id: 'video', type: 'video', name: '原片'}, {id: 'front', type: 'image', name: '主图'}, {id: 'extra', type: 'image', name: '辅助'}], saved);
    expect(media.map(item => item.id)).toEqual(['video', 'front', 'a-image', 'b-image', 'extra']);
    expect(media[2].url).toBe('/preview-a');
  });
  it('主动移除固定图仅删除其标记并同步后续编号，其他文字与空白逐字保留', () => {
    const raw = ' \n编辑 {{Mixed 1}}，{{Mixed 3}} 与 {{Mixed 4}}。\n\n';
    const result = removeFixedPackReference(raw, refs, 'a-image', 2);
    expect(result.prompt).toBe(' \n编辑 {{Mixed 1}}， 与 {{Mixed 3}}。\n\n');
    expect(result.references).toEqual([{...refs[1], token: '{{Mixed 3}}'}]);
  });
});
