import {describe, expect, it} from 'vitest';
import type {SpecSlotOut} from '@/shared/api/schema';
import {
  DEFAULT_RANDOMNESS,
  displaySlotValues,
  isOptionalSlot,
  mergeSlotValues,
  pickRandomTextChoices,
  suppressedSlotKeys,
  visibleSlots,
} from './randomness';

/**
 * 「随机性」三档的纯函数层。
 *
 * 这层的语义是整页唯一口径：右栏显示哪些槽位、真正提交什么素材、随机档
 * 抽哪段文案，全从这里出。任何别处再写一遍 `minCount === 0` 都会让界面
 * 与提交内容对不上（而那种错只在生成结果里显形）。
 */

function slotOf(extra: Partial<SpecSlotOut> = {}): SpecSlotOut {
  return {
    key: 'image_1',
    type: 'image',
    binding: 'user',
    minCount: 1,
    maxCount: 1,
    label: '图',
    tagIds: [],
    ...extra,
  };
}

/** 典型模板：1 必填 + 1 可选（配了随机文案）+ 1 固定。 */
const SLOTS: SpecSlotOut[] = [
  slotOf({key: 'hero', minCount: 1}),
  slotOf({key: 'copy', minCount: 0, randomTexts: ['详细场景描述', '简单场景描述']}),
  slotOf({key: 'source', binding: 'fixed', assetId: 'a9'}),
];

describe('randomness · 判据只有 minCount === 0', () => {
  it('minCount 为 0 才是可选', () => {
    expect(isOptionalSlot(slotOf({minCount: 0}))).toBe(true);
    expect(isOptionalSlot(slotOf({minCount: 1}))).toBe(false);
  });

  it('没写 minCount（undefined）按必填算', () => {
    expect(isOptionalSlot(slotOf({minCount: undefined}))).toBe(false);
  });

  it('默认档是均衡', () => {
    expect(DEFAULT_RANDOMNESS).toBe('balanced');
  });
});

describe('randomness · 可见槽位', () => {
  it('随机档与均衡档：只列必填槽位，可选的整个不出现', () => {
    expect(visibleSlots(SLOTS, 'balanced').map(s => s.key)).toEqual(['hero']);
    expect(visibleSlots(SLOTS, 'random').map(s => s.key)).toEqual(['hero']);
  });

  it('自定义档：必填与可选都列', () => {
    expect(visibleSlots(SLOTS, 'custom').map(s => s.key)).toEqual(['hero', 'copy']);
  });

  it('固定槽位三档都不列', () => {
    for (const mode of ['balanced', 'random', 'custom'] as const) {
      expect(visibleSlots(SLOTS, mode).map(s => s.key)).not.toContain('source');
    }
  });
});

describe('randomness · 抑制传素材的槽位', () => {
  it('随机档与均衡档都抑制可选槽位（不显示 + 不传，两件事一起做）', () => {
    expect(suppressedSlotKeys(SLOTS, 'balanced')).toEqual(['copy']);
    expect(suppressedSlotKeys(SLOTS, 'random')).toEqual(['copy']);
  });

  it('自定义档不抑制任何槽位', () => {
    expect(suppressedSlotKeys(SLOTS, 'custom')).toEqual([]);
  });
});

describe('randomness · 随机档抽文案', () => {
  it('只抽有随机文案的可选槽位', () => {
    const choices = pickRandomTextChoices(SLOTS, '', 1);
    expect(Object.keys(choices)).toEqual(['copy']);
  });

  it('下标落在文案范围内', () => {
    for (const seed of [1, 2, 3, 99]) {
      const choices = pickRandomTextChoices(SLOTS, '', seed);
      expect(choices.copy).toBeGreaterThanOrEqual(0);
      expect(choices.copy).toBeLessThan(2);
    }
  });

  it('同一颗种子 → 同一份选择（渲染不跳变）', () => {
    expect(pickRandomTextChoices(SLOTS, '', 42)).toEqual(
      pickRandomTextChoices(SLOTS, '', 42),
    );
  });

  it('没配随机文案的槽位不抽', () => {
    const slots = [slotOf({key: 'copy', minCount: 0, randomTexts: []})];
    expect(pickRandomTextChoices(slots, '', 1)).toEqual({});
  });

  it('只有一段文案时抽 0（后端取那唯一一段）', () => {
    const slots = [slotOf({key: 'copy', minCount: 0, randomTexts: ['唯一一段']})];
    expect(pickRandomTextChoices(slots, '', 7)).toEqual({copy: 0});
  });
});

describe('randomness · 合并成真正提交的那份素材', () => {
  it('被抑制的槽位整个不出现（随机/均衡档的可选槽位）', () => {
    expect(mergeSlotValues({copy: ['m1'], hero: ['h1']}, ['copy'])).toEqual({hero: ['h1']});
  });

  it('没动过的槽位不占键', () => {
    expect(mergeSlotValues({copy: undefined}, [])).toEqual({});
  });

  it('手动挑空的槽位不占键', () => {
    expect(mergeSlotValues({hero: []}, [])).toEqual({});
  });
});

describe('randomness · 界面上显示哪份', () => {
  it('显示手动挑的；没挑就是空（随机档不填素材）', () => {
    expect(displaySlotValues('copy', {copy: ['m1']})).toEqual(['m1']);
    expect(displaySlotValues('copy', {})).toEqual([]);
    expect(displaySlotValues('copy', {copy: []})).toEqual([]);
  });
});
