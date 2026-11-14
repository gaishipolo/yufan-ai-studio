import { describe, expect, it } from 'vitest';
import { blankDraft, blankPack, fromManifest, moveItem, toPayload } from './manifestDraft';
import type { ManifestOut } from '@/shared/api/schema';

describe('封装草稿', () => {
  it('参考图顺序决定编号：第 1 张参考图永远是 {{Mixed 2}}', () => {
    const draft = blankDraft();
    draft.packs = [
      {
        ...blankPack('product'),
        key: 'pack-1',
        name: '连衣裙',
        refs: [
          { assetId: 'a1', role: 'front', description: '' },
          { assetId: 'a2', role: 'back', description: '' },
        ],
      },
    ];
    draft.functions = [
      {
        key: 'fn-1',
        name: '换装',
        packKey: 'pack-1',
        prompt: '{{Mixed 1}} 与 {{Mixed 2}}',
        useRoles: true,
        aspectRatio: '9:16',
        duration: '',
        scenePrompt: '',
      },
    ];

    const payload = toPayload(draft);
    expect(payload.packs?.[0].references?.map(item => item.assetId)).toEqual(['a1', 'a2']);
    expect(payload.functions?.[0].bindings).toEqual({
      '{{Mixed 1}}': 'source_video',
      '{{Mixed 2}}': 'front',
      '{{Mixed 3}}': 'back',
    });

    // 换序之后键按新顺序重编，绑定跟着走 —— 编号与图片永远对得上。
    draft.packs[0].refs = moveItem(draft.packs[0].refs, 0, 1);
    const swapped = toPayload(draft);
    expect(swapped.packs?.[0].references?.map(item => item.assetId)).toEqual(['a2', 'a1']);
    expect(swapped.functions?.[0].bindings).toEqual({
      '{{Mixed 1}}': 'source_video',
      '{{Mixed 2}}': 'back',
      '{{Mixed 3}}': 'front',
    });
  });

  it('逐图说明只在非空时发送', () => {
    const draft = blankDraft();
    draft.packs = [
      {
        ...blankPack('character'),
        key: 'pack-1',
        name: '模特',
        refs: [
          { assetId: 'a1', role: 'front', description: '  正面全身，无遮挡  ' },
          { assetId: 'a2', role: 'side', description: '   ' },
        ],
      },
    ];
    const refs = toPayload(draft).packs?.[0].references ?? [];
    expect(refs[0].description).toBe('正面全身，无遮挡');
    expect(refs[1]).not.toHaveProperty('description');
  });

  it('已发布配置读回草稿时保留说明与顺序', () => {
    const out: ManifestOut = {
      id: 'm1',
      key: 'tpl-1',
      name: '演示',
      description: '',
      ownerId: 'u1',
      status: 'published',
      version: 1,
      revision: 3,
      fingerprint: 'fp-1',
      createdAt: '2026-11-14T00:00:00Z',
      updatedAt: '2026-11-14T00:00:00Z',
      video: { assetId: 'src-1' },
      packs: [
        {
          key: 'pack-1',
          kind: 'product',
          name: '连衣裙',
          packId: 'run-pack-1',
          references: [
            { key: 'ref1', assetId: 'a2', role: 'back', description: '背面' },
            { key: 'ref2', assetId: 'a1', role: 'front' },
          ],
        },
      ],
      functions: [],
    };
    const draft = fromManifest(out);
    expect(draft.packs[0].mode).toBe('reuse');
    expect(draft.packs[0].refs).toEqual([
      { assetId: 'a2', role: 'back', description: '背面' },
      { assetId: 'a1', role: 'front', description: '' },
    ]);
  });

  it('移动越界或原地不动时返回原数组', () => {
    const list = ['a', 'b', 'c'];
    expect(moveItem(list, 0, 0)).toBe(list);
    expect(moveItem(list, 0, 5)).toBe(list);
    expect(moveItem(list, -1, 1)).toBe(list);
    expect(moveItem(list, 2, 0)).toEqual(['c', 'a', 'b']);
  });
});
