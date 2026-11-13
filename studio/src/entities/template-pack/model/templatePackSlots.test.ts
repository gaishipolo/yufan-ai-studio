import { describe, expect, it } from 'vitest';
import {
  citedPositions,
  initialSlots,
  positionOf,
  toSlotIn,
  tokenOf,
  validateSlots,
  type TemplatePackSlotDraft,
} from './templatePackSlots';

/**
 * 槽位编辑态的纯逻辑。
 *
 * 这里守的是**作者在保存前就该看到的问题**：编号不连续、提示词引用了
 * 不存在的槽位、第 1 槽不是参考视频。
 *
 * 注意：这些校验只是**给作者看的即时反馈**，最终判定在后端 ——
 * 后端那一份才是契约。所以这里的期望不能比后端更宽松。
 */
describe('槽位编号', () => {
  it('序号 → 占位符', () => {
    expect(tokenOf(1)).toBe('{{Mixed 1}}');
    expect(tokenOf(12)).toBe('{{Mixed 12}}');
  });

  it('占位符 → 序号，认不出来时是 0', () => {
    expect(positionOf('{{Mixed 3}}')).toBe(3);
    expect(positionOf('{{Mixed 0}}')).toBe(0);
    expect(positionOf('{{mixed 3}}')).toBe(0);
    expect(positionOf('Mixed 3')).toBe(0);
    expect(positionOf('{{Mixed 3}')).toBe(0);
  });
});

describe('提示词引用', () => {
  it('按出现顺序去重', () => {
    expect(citedPositions('用 {{Mixed 2}}，再 {{Mixed 1}}，最后 {{Mixed 2}}')).toEqual([2, 1]);
  });

  it('半截的不算引用', () => {
    expect(citedPositions('写 {{Mixed 没写完')).toEqual([]);
    expect(citedPositions('口语说法 Mixed 3')).toEqual([]);
  });
});

describe('初始槽位', () => {
  it('第 1 槽是参考视频（固定素材），第 2 槽是筛选槽位', () => {
    const slots = initialSlots();
    expect(slots).toHaveLength(2);
    expect(slots[0].slotKind).toBe('fixed');
    expect(slots[0].mediaType).toBe('video');
    expect(slots[0].token).toBe('{{Mixed 1}}');
    expect(slots[1].slotKind).toBe('filtered');
    expect(slots[1].filterKind).toBe('image');
  });
});

describe('编辑态 → 入参', () => {
  it('固定槽位带素材，筛选槽位带条件，两者的字段互斥', () => {
    const slots = initialSlots();
    slots[0].assetId = 'abc';
    slots[1].filterKind = 'video';
    slots[1].filterTagIds = ['t1', 't2'];

    const [first, second] = slots.map(toSlotIn);

    expect(first.assetId).toBe('abc');
    expect(first.filterKind).toBeUndefined();
    expect(second.filterKind).toBe('video');
    expect(second.filterTagIds).toEqual(['t1', 't2']);
    expect(second.assetId).toBeUndefined();
  });

  it('筛选槽位的标签组合方式固定是 any', () => {
    const slots = initialSlots();
    expect(toSlotIn(slots[1]).filterTagMatch).toBe('any');
  });
});

describe('本地校验', () => {
  const prompt = '按 {{Mixed 1}} 的节奏，用 {{Mixed 2}} 做主图';

  it('合法的配置没有错误', () => {
    const slots = initialSlots();
    slots[0].assetId = 'vid';
    expect(validateSlots(slots, prompt)).toEqual({});
  });

  it('第 1 槽没绑视频 → 报错', () => {
    const slots = initialSlots();
    expect(validateSlots(slots, prompt)._form).toBeUndefined();
    expect(Object.keys(validateSlots(slots, prompt)).length).toBeGreaterThan(0);
  });

  it('固定槽位没选素材 → 报在那个槽位上', () => {
    const slots = initialSlots();
    slots[0].assetId = 'vid';
    slots[1].slotKind = 'fixed';
    slots[1].assetId = '';
    const errors = validateSlots(slots, prompt);
    expect(errors['{{Mixed 2}}']).toContain('固定素材');
  });

  it('筛选槽位没定类别 → 报错', () => {
    const slots = initialSlots();
    slots[0].assetId = 'vid';
    slots[1].filterKind = '';
    expect(validateSlots(slots, prompt)['{{Mixed 2}}']).toContain('素材类别');
  });

  it('提示词引用了没有对应槽位的编号 → 报错', () => {
    const slots = initialSlots();
    slots[0].assetId = 'vid';
    const errors = validateSlots(slots, '用 {{Mixed 5}} 做主图');
    expect(errors._prompt).toContain('{{Mixed 5}}');
  });

  it('编号不连续 → 报错', () => {
    const slots: TemplatePackSlotDraft[] = [
      {...initialSlots()[0], assetId: 'vid'},
      {...initialSlots()[1], token: '{{Mixed 3}}'},
    ];
    expect(Object.keys(validateSlots(slots, prompt)).length).toBeGreaterThan(0);
  });

  it('第 1 槽不是参考视频 → 报错', () => {
    const slots = initialSlots();
    slots[0].slotKind = 'filtered';
    slots[0].filterKind = 'image';
    const errors = validateSlots(slots, prompt);
    expect(errors['{{Mixed 1}}']).toContain('参考视频');
  });

  it('一个槽位都没有 → 报错', () => {
    expect(validateSlots([], prompt)._form).toContain('至少');
  });
});
