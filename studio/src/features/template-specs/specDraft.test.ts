import { describe, expect, it } from 'vitest';
import type { TemplateSpecOut } from '@/shared/api/schema';
import {
  blankSlot,
  blankSpec,
  fromSpecOut,
  insertRef,
  nextSlotKey,
  normalize,
  rekeySlot,
  referenceChips,
} from './specDraft';

describe('模板规格草稿', () => {
  it('新草稿自带一个图片槽位，位置从 0 开始', () => {
    const spec = blankSpec();
    const slots = spec.slots ?? [];
    expect(slots).toHaveLength(1);
    expect(slots[0].position).toBe(0);
    expect(slots[0].type).toBe('image');
    expect(slots[0].binding).toBe('user');
  });

  it('槽位 key 自动生成且可读：同类型递增、不同类型不撞', () => {
    // 界面上不显示 key，但提示词里的 `{{slot:image_1}}` 要人一眼能读。
    expect(blankSlot(0, 'image', []).key).toBe('image_1');
    const slots = [blankSlot(0, 'image', [])];
    expect(nextSlotKey(slots, 'image')).toBe('image_2');
    expect(nextSlotKey(slots, 'video')).toBe('video_1');
    // 删掉中间那个之后序号可以复用，不要求连续
    slots.push({...blankSlot(1, 'image', slots), key: 'image_3'});
    expect(nextSlotKey(slots, 'image')).toBe('image_2');
  });

  it('插入引用时只在需要的一侧补空格，并把光标放到引用之后', () => {
    // 光标在末尾：只需前导空格，不该凭空多一个尾空格。
    const atEnd = insertRef('替换成', '{{slot:hero}}', 3);
    expect(atEnd.text).toBe('替换成 {{slot:hero}}');
    expect(atEnd.caret).toBe('替换成 {{slot:hero}}'.length);

    // 光标在中间：两侧都要断开，否则 token 会和中文粘成一个词。
    const middle = insertRef('替换成同款', '{{slot:hero}}', 3);
    expect(middle.text).toBe('替换成 {{slot:hero}} 同款');
  });

  it('插入到空文本不产生前后空格', () => {
    expect(insertRef('', '{{slot:hero}}', 0).text).toBe('{{slot:hero}}');
  });

  it('视觉槽位与文本槽位都能引用；多值槽位额外给「全部」', () => {
    const spec = blankSpec();
    spec.slots = [
      {...blankSlot(0), key: 'hero', label: '主图', minCount: 1, maxCount: 1},
      {...blankSlot(1), key: 'detail', label: '细节', minCount: 0, maxCount: 3},
      // 文本槽位是"提示词里唯一可变的地方"：编译成 {{Text N}}，必须能引用。
      {...blankSlot(2, 'text', []), key: 'brief', type: 'text', refMode: 'attachment', label: '口播'},
      {...blankSlot(3), key: 'bgm', type: 'audio', refMode: 'attachment'},
    ];
    const chips = referenceChips(spec);
    expect(chips.map(chip => chip.token)).toEqual([
      '{{slot:hero}}',
      '{{slot:detail}}',
      '{{slot:detail*}}',
      '{{slot:brief}}',
    ]);
    // 音频没有编号空间，给入口就等于让用户写出一条必然 400 的引用。
    expect(chips.some(chip => chip.token.includes('bgm'))).toBe(false);
  });

  it('改类型时改名：没被引用过才改，引用过就留着', () => {
    const slots = [{...blankSlot(0), key: 'image_1', type: 'image' as const}];
    // 没引用 → 跟着类型改成 video_1
    const renamed = rekeySlot(slots, 0, 'video', '把 {{slot:other}} 换掉');
    expect(renamed[0].key).toBe('video_1');
    expect(renamed[0].type).toBe('video');
    // 已被引用 → 原名不动，否则提示词里的引用会静默失效
    const kept = rekeySlot(slots, 0, 'video', '把 {{slot:image_1}} 换掉');
    expect(kept[0].key).toBe('image_1');
  });

  it('已发布但没有草稿时，用最近发布版本还原草稿', () => {
    const out: TemplateSpecOut = {
      id: 'm1',
      key: 'demo',
      name: '演示',
      description: '',
      ownerId: 'u1',
      status: 'published',
      revision: 3,
      fingerprint: 'fp',
      currentVersion: 2,
      createdAt: '2026-11-14T00:00:00Z',
      updatedAt: '2026-11-14T00:00:00Z',
      latest: {
        version: 2,
        model: 'libtv-wan3-replacement',
        params: {aspectRatio: '9:16'},
        prompt: '固定\n\n{{slot:hero}}',
        slots: [{...blankSlot(0), key: 'hero'}, {...blankSlot(5), key: 'extra'}],
        fingerprint: 'fp2',
        publishedBy: 'u1',
        publishedAt: '2026-11-14T00:00:00Z',
      },
    };
    const draft = fromSpecOut(out);
    expect(draft.key).toBe('demo');
    expect(draft.revision).toBe(3);
    expect(draft.model).toBe('libtv-wan3-replacement');
    // 位置按数组顺序重排：发布快照里的 position 可能不连续（历史数据）。
    expect((draft.slots ?? []).map(slot => slot.position)).toEqual([0, 1]);
  });

  it('提交前按当前顺序重排 position', () => {
    const spec = blankSpec();
    spec.slots = [{...blankSlot(7), key: 'b'}, {...blankSlot(2), key: 'a'}];
    expect((normalize(spec).slots ?? []).map(slot => [slot.key, slot.position])).toEqual([
      ['b', 0],
      ['a', 1],
    ]);
  });
});
