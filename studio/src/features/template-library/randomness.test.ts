import {describe, expect, it} from 'vitest';
import type {SpecSlotOut} from '@/shared/api/schema';
import {
  DEFAULT_RANDOMNESS,
  adoptAutoValues,
  autoFilledSlots,
  displaySlotValues,
  isOptionalSlot,
  mergeSlotValues,
  releaseCarriedValues,
  suppressedSlotKeys,
  visibleSlots,
} from './randomness';

/**
 * 「随机性」三档的纯函数层。
 *
 * 这层的语义是**整页唯一的口径**：右栏显示哪些槽位、真正提交什么、
 * 缺料怎么算，全从这里出。任何别处再写一遍 `minCount === 0` 都会
 * 让界面与提交内容对不上（而那种错只在生成结果里显形）。
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

/** 一个典型模板：1 必填 + 1 可选 + 1 固定。 */
const SLOTS: SpecSlotOut[] = [
  slotOf({key: 'hero', minCount: 1, maxCount: 3}),
  slotOf({key: 'copy', minCount: 0, maxCount: 1}),
  slotOf({key: 'source', binding: 'fixed', assetId: 'a9'}),
];

describe('randomness · 判据只有 minCount === 0', () => {
  it('minCount 为 0 才是可选', () => {
    expect(isOptionalSlot(slotOf({minCount: 0}))).toBe(true);
    expect(isOptionalSlot(slotOf({minCount: 1}))).toBe(false);
    expect(isOptionalSlot(slotOf({minCount: 2}))).toBe(false);
  });

  it('没写 minCount（undefined）按必填算，不是可选', () => {
    /* 判据写错的话，老模板（后端没返回 minCount）会整个变成「可选」，
       均衡档下就一个槽位都不显示了。 */
    expect(isOptionalSlot(slotOf({minCount: undefined}))).toBe(false);
  });

  it('默认档是均衡', () => {
    expect(DEFAULT_RANDOMNESS).toBe('balanced');
  });
});

describe('randomness · 可见槽位', () => {
  it('均衡档：只列必填槽位，可选的整个不出现', () => {
    expect(visibleSlots(SLOTS, 'balanced').map(s => s.key)).toEqual(['hero']);
  });

  it('随机档与自定义档：必填与可选都列', () => {
    expect(visibleSlots(SLOTS, 'random').map(s => s.key)).toEqual(['hero', 'copy']);
    expect(visibleSlots(SLOTS, 'custom').map(s => s.key)).toEqual(['hero', 'copy']);
  });

  it('固定槽位三档都不列（作者选死的，填表的人改不了）', () => {
    for (const mode of ['balanced', 'random', 'custom'] as const) {
      expect(visibleSlots(SLOTS, mode).map(s => s.key)).not.toContain('source');
    }
  });
});

describe('randomness · 抑制传素材的槽位', () => {
  it('均衡档抑制可选槽位（不显示 + 不传，两件事一起做）', () => {
    expect(suppressedSlotKeys(SLOTS, 'balanced')).toEqual(['copy']);
  });

  it('随机档与自定义档不抑制任何槽位', () => {
    expect(suppressedSlotKeys(SLOTS, 'random')).toEqual([]);
    expect(suppressedSlotKeys(SLOTS, 'custom')).toEqual([]);
  });
});

describe('randomness · 随机档自动填', () => {
  it('按 maxCount 挑（不是 minCount —— 可选槽位的 minCount 是 0）', () => {
    const slots = [slotOf({key: 'copy', minCount: 0, maxCount: 3})];
    const filled = autoFilledSlots(slots, {copy: ['a', 'b', 'c', 'd']}, pool => pool.slice(0, 3));
    expect(filled).toEqual({copy: ['a', 'b', 'c']});
  });

  it('候选池为空时不填（宁可空着，也不要凑一个假随机）', () => {
    const slots = [slotOf({key: 'copy', minCount: 0, maxCount: 1})];
    expect(autoFilledSlots(slots, {}, pool => pool)).toEqual({});
  });

  it('必填槽位不被自动填（那是使用者自己的事）', () => {
    const filled = autoFilledSlots(SLOTS, {hero: ['a', 'b']}, pool => pool);
    expect(filled).toEqual({});
  });
});

describe('randomness · 合并成真正提交的那份', () => {
  it('手动挑的盖过系统随机挑的', () => {
    const out = mergeSlotValues({copy: ['m1']}, {copy: ['r1', 'r2']}, []);
    expect(out).toEqual({copy: ['m1']});
  });

  it('手动挑空了也能盖掉系统挑的（`undefined` 才是「没动过」）', () => {
    /* 这条是随机档的关键：使用者把系统挑的那张删掉，必须真的删掉，
       而不是「没动过 → 系统那张还在」。 */
    const out = mergeSlotValues({copy: []}, {copy: ['r1']}, []);
    expect(out).toEqual({});
  });

  it('没动过的槽位保留系统挑的', () => {
    const out = mergeSlotValues({}, {copy: ['r1']}, []);
    expect(out).toEqual({copy: ['r1']});
  });

  it('被抑制的槽位整个不出现（均衡档的可选槽位）', () => {
    const out = mergeSlotValues({copy: ['m1']}, {copy: ['r1']}, ['copy']);
    expect(out).toEqual({});
  });

  it('空的槽位不占键', () => {
    const out = mergeSlotValues({hero: []}, {}, []);
    expect(out).toEqual({});
    expect('hero' in out).toBe(false);
  });
});

describe('randomness · 界面上显示哪份', () => {
  it('手动挑过的显示手动挑的', () => {
    expect(displaySlotValues('copy', {copy: ['m1']}, {copy: ['r1']})).toEqual(['m1']);
  });

  it('没动过的在随机档显示系统挑的', () => {
    expect(displaySlotValues('copy', {}, {copy: ['r1']})).toEqual(['r1']);
  });

  it('手动挑空的显示空（不是回落到系统挑的）', () => {
    expect(displaySlotValues('copy', {copy: []}, {copy: ['r1']})).toEqual([]);
  });
});

describe('randomness · 切档时的接管与交还', () => {
  it('接管：系统随机填的变成手挑的', () => {
    /* 不接管的话，「随机 → 自定义」一切换，那几张就从界面上消失了 ——
       使用者明明刚看到它们填在那里。 */
    const result = adoptAutoValues({}, {copy: ['r1']});
    expect(result.values).toEqual({copy: ['r1']});
    expect(result.carried).toEqual(['copy']);
  });

  it('接管：不覆盖使用者亲手挑过的', () => {
    const result = adoptAutoValues({copy: ['m1']}, {copy: ['r1']});
    expect(result.values).toEqual({copy: ['m1']});
    expect(result.carried).toEqual([]);
  });

  it('接管：手动挑空的也不覆盖（那是「我不要这张」）', () => {
    const result = adoptAutoValues({copy: []}, {copy: ['r1']});
    expect(result.values).toEqual({copy: []});
    expect(result.carried).toEqual([]);
  });

  it('交还：接管来的被清掉，让新的随机接管', () => {
    /* 不交还的后果：「随机 → 自定义 → 随机」绕一圈回来还是原来那几张，
       「随机」就成了摆设。 */
    expect(releaseCarriedValues({copy: ['r1']}, ['copy'])).toEqual({});
  });

  it('交还：使用者亲手挑的不动', () => {
    expect(releaseCarriedValues({copy: ['m1'], hero: ['h1']}, ['hero'])).toEqual({
      copy: ['m1'],
    });
  });

  it('接管 → 交还 一圈下来回到原点', () => {
    const adopted = adoptAutoValues({}, {copy: ['r1']});
    const released = releaseCarriedValues(adopted.values, adopted.carried);
    expect(released).toEqual({});
  });
});
