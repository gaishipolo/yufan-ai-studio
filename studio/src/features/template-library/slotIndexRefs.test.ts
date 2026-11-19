import {describe, expect, it} from 'vitest';
import type {SpecSlotOut} from '@/shared/api/schema';
import {
  balancedPinnedKeys,
  indexedRefCounts,
  requiredSlotCount,
  slotRefModes,
} from './slotIndexRefs';

/**
 * 「均衡」档下不能被让给模型的槽位。
 *
 * 这些是**编译期才会报错**的组合 —— 界面上看不出区别，只有生成时才炸，
 * 而那句报错（「第 2 张不存在」）与「我用的是均衡模式」毫无关系。
 */

function slotOf(extra: Partial<SpecSlotOut> = {}): SpecSlotOut {
  return {
    key: 'copy',
    type: 'image',
    binding: 'user',
    minCount: 0,
    maxCount: 2,
    label: '参考图',
    tagIds: [],
    ...extra,
  };
}

describe('slotRefModes · 提示词里怎么引用槽位', () => {
  it('认得三种写法', () => {
    const modes = slotRefModes('{{slot:a}} {{slot:b*}} {{slot:c.2}}');
    expect(modes.a).toEqual(new Set(['']));
    expect(modes.b).toEqual(new Set(['*']));
    expect(modes.c).toEqual(new Set(['.2']));
  });

  it('两边允许空格（与后端同一形状）', () => {
    /* 后端 `compile.py::_SLOT_REF` 允许 `{{ slot:x }}`。这里若不容空格，
       界面会以为「没被引用」，而后端会照着展开 —— 均衡档就 400 了。 */
    const modes = slotRefModes('{{ slot:x }}');
    expect(modes.x).toEqual(new Set(['']));
  });

  it('没被引用的槽位不出现', () => {
    expect(slotRefModes('没有引用').x).toBeUndefined();
  });

  /* 作者写的是 `{{Mixed N}}`（第 N 个图片/成片槽位）。这里必须换算回 key，
     否则「均衡档能不能省掉这个槽位」整条判据都会失效 —— 界面以为没被引用，
     用户点生成时后端却按单张引用报「引用了未填素材的槽位」。 */
  it('`{{Mixed N}}` 按槽位顺序解析到 key', () => {
    const slots = [
      slotOf({key: 'source', type: 'video', binding: 'fixed', maxCount: 1}),
      slotOf({key: 'hero'}),
      slotOf({key: 'copy', type: 'text', refMode: 'attachment'}),
    ];
    const modes = slotRefModes('原片 {{Mixed 1}}，人物 {{Mixed 2}}', slots);

    expect(modes.source).toEqual(new Set(['']));
    expect(modes.hero).toEqual(new Set(['']));
    // 文本槽位不占 Mixed 编号：`Mixed 3` 不存在，`copy` 也不该被算进来
    expect(modes.copy).toBeUndefined();
  });

  it('`{{Mixed 2.3}}` 说明第 2 个视觉槽位至少要 3 张', () => {
    const slots = [slotOf({key: 'hero'}), slotOf({key: 'detail'})];
    expect(indexedRefCounts('{{Mixed 2.3}}', slots)).toEqual({detail: 3});
    expect(requiredSlotCount(slots[1], '{{Mixed 2.3}}', slots)).toBe(3);
  });
});

describe('balancedPinnedKeys · 均衡档下必须传的槽位', () => {
  it('没被引用的可选槽位：可以省掉', () => {
    expect(balancedPinnedKeys('随便写点什么', [slotOf()])).toEqual([]);
  });

  it('只被整组引用（{{slot:x*}}）：可以省掉（后端降级为空）', () => {
    expect(balancedPinnedKeys('{{slot:copy*}}', [slotOf()])).toEqual([]);
  });

  it('被单张引用且写了「未选时文案」：可以省掉', () => {
    const slots = [slotOf({emptyText: '主角是穿红裙的年轻女性'})];
    expect(balancedPinnedKeys('{{slot:copy}}', slots)).toEqual([]);
  });

  it('被单张引用但没写「未选时文案」：必须传（否则编译期 400）', () => {
    expect(balancedPinnedKeys('{{slot:copy}}', [slotOf()])).toEqual(['copy']);
  });

  it('被「第 N 张」引用：必须传（没有「第 2 张」这回事）', () => {
    expect(balancedPinnedKeys('{{slot:copy.2}}', [slotOf()])).toEqual(['copy']);
  });

  it('「未选时文案」只有空白也算没写', () => {
    const slots = [slotOf({emptyText: '   '})];
    expect(balancedPinnedKeys('{{slot:copy}}', slots)).toEqual(['copy']);
  });

  it('同一个槽位既整组引用又第 N 张引用：按「必须传」处理', () => {
    expect(balancedPinnedKeys('{{slot:copy*}} {{slot:copy.1}}', [slotOf()])).toEqual([
      'copy',
    ]);
  });
});

describe('indexedRefCounts · 「第 N 张」意味着至少 N 张', () => {
  it('没被索引引用就是 0', () => {
    expect(indexedRefCounts('{{slot:copy}} {{slot:other*}}')).toEqual({});
  });

  it('取所有索引里的最大值', () => {
    expect(indexedRefCounts('{{slot:copy.2}} {{slot:copy.5}}')['copy']).toBe(5);
  });

  it('只数索引引用，单张与整组不算', () => {
    expect(indexedRefCounts('{{slot:copy}} {{slot:copy*}}')).toEqual({});
  });

  it('不同槽位各算各的', () => {
    expect(indexedRefCounts('{{slot:a.2}} {{slot:b.3}}')).toEqual({a: 2, b: 3});
  });
});

describe('requiredSlotCount · 槽位实际最少要几张', () => {
  it('平时就是 minCount', () => {
    expect(requiredSlotCount(slotOf({minCount: 1}), '随便')).toBe(1);
  });

  it('minCount 没写就按 1 张', () => {
    expect(requiredSlotCount(slotOf({minCount: undefined}), '随便')).toBe(1);
  });

  /* 这条是真实缺口：索引引用是**硬约束**（后端取 `owned[index - 1]`，
     越界就 400），而 `minCount` 只是作者写���的意图。两者必须取大的。 */
  it('提示词写了「第 3 张」：实际下限是 3（哪怕 minCount 是 0）', () => {
    expect(requiredSlotCount(slotOf({minCount: 0}), '{{slot:copy.3}}')).toBe(3);
  });

  it('minCount 更大时取 minCount', () => {
    expect(requiredSlotCount(slotOf({minCount: 5}), '{{slot:copy.3}}')).toBe(5);
  });

  it('索引引用与档位无关（不看均衡）', () => {
    /* 三个档位下同一个提示词，下限都一样 —— 索引是提示词里的硬约束。 */
    const prompt = '{{slot:copy.2}}';
    expect(requiredSlotCount(slotOf({minCount: 0}), prompt)).toBe(2);
  });

  it('别的槽位的索引引用不影响这个槽位', () => {
    expect(requiredSlotCount(slotOf({minCount: 0}), '{{slot:other.4}}')).toBe(0);
  });

  it('单张引用且没写兜底文案：至少 1 张（不填就 400）', () => {
    expect(requiredSlotCount(slotOf({minCount: 0}), '{{slot:copy}}')).toBe(1);
  });

  it('单张引用且写了兜底文案：下限回到 minCount（可以不填）', () => {
    const slots = slotOf({minCount: 0, emptyText: '主角是穿红裙的年轻女性'});
    expect(requiredSlotCount(slots, '{{slot:copy}}')).toBe(0);
  });

  it('整组引用：下限回到 minCount（没填时降级为空）', () => {
    expect(requiredSlotCount(slotOf({minCount: 0}), '{{slot:copy*}}')).toBe(0);
  });

  it('单张引用 + 兜底文案 + minCount 是 1：下限还是 1', () => {
    const slots = slotOf({minCount: 1, emptyText: '兜底'});
    expect(requiredSlotCount(slots, '{{slot:copy}}')).toBe(1);
  });
});
