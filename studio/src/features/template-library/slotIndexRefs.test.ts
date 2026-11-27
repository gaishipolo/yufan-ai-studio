import {describe, expect, it} from 'vitest';
import type {SpecSlotOut} from '@/shared/api/schema';
import {
  balancedPinnedKeys,
  requiredSlotCount,
  slotRefModes,
} from './slotIndexRefs';

/**
 * 「均衡」档下不能被让给模型的槽位。
 *
 * 这些是**编译期才会报错**的组合 —— 界面上看不出区别，只有生成时才炸，
 * 而那句报错与「我用的是均衡模式」毫无关系。
 *
 * 一槽一素材之后，引用只剩**一种**写法（「整组」「第 N 张」已被结构校验拒，
 * 见后端 rules.py::_validate_ref_modifier），所以这里的判据只有一条：
 * **被单张引用、而作者没写「未选时文案」→ 必须传**。
 */

function slotOf(extra: Partial<SpecSlotOut> = {}): SpecSlotOut {
  return {
    key: 'copy',
    type: 'image',
    binding: 'user',
    minCount: 0,
    maxCount: 1,
    label: '参考图',
    tagIds: [],
    ...extra,
  };
}

describe('slotRefModes · 提示词里怎么引用槽位', () => {
  it('认得单个引用', () => {
    const modes = slotRefModes('{{slot:a}} {{slot:b}}');
    expect(modes.a).toEqual(new Set(['']));
    expect(modes.b).toEqual(new Set(['']));
  });

  it('两边允许空格（与后端同一形状）', () => {
    /* 后端 rules.py::_SLOT_REF 允许 `{{ slot:x }}`。这里若不容空格，
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
});

describe('balancedPinnedKeys · 均衡档下必须传的槽位', () => {
  it('没被引用的可选槽位：可以省掉', () => {
    expect(balancedPinnedKeys('随便写点什么', [slotOf()])).toEqual([]);
  });

  it('被单张引用且写了「未选时文案」：可以省掉', () => {
    const slots = [slotOf({emptyText: '主角是穿红裙的年轻女性'})];
    expect(balancedPinnedKeys('{{slot:copy}}', slots)).toEqual([]);
  });

  it('被单张引用但没写「未选时文案」：必须传（否则编译期 400）', () => {
    expect(balancedPinnedKeys('{{slot:copy}}', [slotOf()])).toEqual(['copy']);
  });

  it('「未选时文案」只有空白也算没写', () => {
    const slots = [slotOf({emptyText: '   '})];
    expect(balancedPinnedKeys('{{slot:copy}}', slots)).toEqual(['copy']);
  });
});

describe('requiredSlotCount · 槽位实际最少要几张', () => {
  it('平时就是 minCount', () => {
    expect(requiredSlotCount(slotOf({minCount: 1}), '随便')).toBe(1);
  });

  it('minCount 没写就按 1 张', () => {
    expect(requiredSlotCount(slotOf({minCount: undefined}), '随便')).toBe(1);
  });

  it('单张引用且没写兜底文案：至少 1 张（不填就 400）', () => {
    expect(requiredSlotCount(slotOf({minCount: 0}), '{{slot:copy}}')).toBe(1);
  });

  it('单张引用且写了兜底文案：下限回到 minCount（可以不填）', () => {
    const slots = slotOf({minCount: 0, emptyText: '主角是穿红裙的年轻女性'});
    expect(requiredSlotCount(slots, '{{slot:copy}}')).toBe(0);
  });

  it('单张引用 + 兜底文案 + minCount 是 1：下限还是 1', () => {
    const slots = slotOf({minCount: 1, emptyText: '兜底'});
    expect(requiredSlotCount(slots, '{{slot:copy}}')).toBe(1);
  });
});
