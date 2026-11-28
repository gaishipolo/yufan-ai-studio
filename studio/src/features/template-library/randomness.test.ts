import {describe, expect, it} from 'vitest';
import type {SpecSlotOut} from '@/shared/api/schema';
import {
  DEFAULT_RANDOMNESS,
  displaySlotValues,
  hasRewritableSlots,
  isOptionalSlot,
  mergeSlotValues,
  resolveRandomness,
  rewritableSlotKeys,
  suppressedSlotKeys,
  visibleSlots,
} from './randomness';

/**
 * 「随机性」三档的纯函数层。
 *
 * 这层的语义是整页唯一口径：右栏显示哪些槽位、真正提交什么素材、随机档
 * 能不能选，全从这里出。任何别处再写一遍 `minCount === 0` 都会让界面
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

/** 典型模板：1 必填 + 1 可选 + 1 固定。 */
const SLOTS: SpecSlotOut[] = [
  slotOf({key: 'hero', minCount: 1}),
  slotOf({key: 'copy', minCount: 0, emptyText: '详细场景描述'}),
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

const VERSION = {
  prompt: '主图 {{slot:hero}}，参考 {{slot:copy}}',
  slots: SLOTS,
};

describe('randomness · 可改写的可选槽位', () => {
  it('可选、有插入点、有兜底文案 → 可改写', () => {
    expect(rewritableSlotKeys(VERSION)).toEqual(['copy']);
    expect(hasRewritableSlots(VERSION)).toBe(true);
  });

  it('作者写法 {{Mixed N}} 也能识别插入点', () => {
    expect(
      rewritableSlotKeys({prompt: '主图 {{Mixed 1}}，参考 {{Mixed 2}}', slots: SLOTS}),
    ).toEqual(['copy']);
  });

  it('没有插入点 / 没有兜底文案 / 不是 mixed / 被固定 → 不进入集合', () => {
    expect(hasRewritableSlots({prompt: '主图 {{slot:hero}}', slots: SLOTS})).toBe(false);

    const noText = {
      prompt: VERSION.prompt,
      slots: SLOTS.map(slot => (slot.key === 'copy' ? {...slot, emptyText: ''} : slot)),
    };
    expect(hasRewritableSlots(noText)).toBe(false);

    const attachment = {
      prompt: VERSION.prompt,
      slots: SLOTS.map(slot =>
        slot.key === 'copy' ? {...slot, refMode: 'attachment' as const} : slot,
      ),
    };
    expect(hasRewritableSlots(attachment)).toBe(false);

    const fixed = {
      prompt: VERSION.prompt,
      slots: SLOTS.map(slot =>
        slot.key === 'copy' ? {...slot, binding: 'fixed' as const} : slot,
      ),
    };
    expect(hasRewritableSlots(fixed)).toBe(false);
  });

  it('没有版本 / 空版本 → 不可用', () => {
    expect(hasRewritableSlots(null)).toBe(false);
    expect(hasRewritableSlots(undefined)).toBe(false);
    expect(hasRewritableSlots({prompt: '', slots: []})).toBe(false);
  });
});

describe('randomness · 档位回落到实际生效的那一档', () => {
  /* 换模板后 state 可能还停在 `random`，而新模板没有可改写槽位。派生写法让
     「选中的档」与「提交标记」永远一致 —— 用 effect 同步会漏一个渲染窗口，
     那期间提交会被后端 400。 */
  it('选了随机、但没有可改写槽位 → 按默认档（均衡）走', () => {
    expect(resolveRandomness('random', {prompt: '主图 {{slot:hero}}', slots: SLOTS}))
      .toBe('balanced');
    expect(resolveRandomness('random', {prompt: '', slots: []})).toBe('balanced');
    expect(resolveRandomness('random', null)).toBe('balanced');
  });

  it('有可改写槽位就是随机；另外两档不受影响', () => {
    expect(resolveRandomness('random', VERSION)).toBe('random');
    expect(resolveRandomness('balanced', VERSION)).toBe('balanced');
    expect(resolveRandomness('custom', VERSION)).toBe('custom');
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
