import { describe, expect, it } from 'vitest';
import type { ModelSpec } from '../model/types';
import {
  initialParamValues,
  isEmptyParamValue,
  paramControlsFor,
  parseNumericInput,
  resolveParamValues,
} from './paramForm';

function model(paramSchema: ModelSpec['paramSchema']): ModelSpec {
  return {
    id: 'm1',
    label: 'M1',
    capability: 'image',
    provider: 'echo',
    isDefault: true,
    paramSchema,
  };
}

describe('paramControlsFor · 不认识的字段也能渲染', () => {
  it('enum 取白名单，默认值必须落在选项里', () => {
    const [size] = paramControlsFor(
      model({
        size: { type: 'enum', options: ['1024x1024', '1024x1536'], default: '1024x1536' },
      }),
    );
    expect(size?.key).toBe('size');
    expect(size?.label).toBe('尺寸');
    expect(size?.options.map((o) => o.value)).toEqual(['1024x1024', '1024x1536']);
    expect(size?.defaultValue).toBe('1024x1536');
  });

  it('default 不在选项里时回落到第一个，避免下拉显示空白', () => {
    const [size] = paramControlsFor(
      model({ size: { type: 'enum', options: ['512x512'], default: '2048x2048' } }),
    );
    expect(size?.defaultValue).toBe('512x512');
  });

  it('int 按 min/max/step 展开档位，并带单位', () => {
    const [count] = paramControlsFor(model({ count: { type: 'int', min: 1, max: 4 } }));
    expect(count?.options.map((o) => o.value)).toEqual(['1', '2', '3', '4']);
    expect(count?.options[1]?.label).toBe('2 张');
  });

  it('上限不会被 step 跳过', () => {
    const [steps] = paramControlsFor(
      model({ steps: { type: 'int', min: 5, max: 12, step: 5 } }),
    );
    expect(steps?.options.map((o) => o.value)).toEqual(['5', '10', '12']);
  });

  it('范围超过 12 档就不做下拉，交给数字输入框', () => {
    const [seed] = paramControlsFor(model({ seed: { type: 'int', min: 0, max: 2147483647 } }));
    expect(seed?.options).toEqual([]);
    expect(seed?.defaultValue).toBe('');
  });

  it('允许 0 起的参数（seed / guidance 不该被强制抬到 1）', () => {
    const [guidance] = paramControlsFor(
      model({ guidance: { type: 'int', min: 0, max: 3, step: 1 } }),
    );
    expect(guidance?.options.map((o) => o.value)).toEqual(['0', '1', '2', '3']);
  });

  it('float 档位保留小数，不做整数截断', () => {
    const [cfg] = paramControlsFor(model({ cfg: { type: 'float', min: 0.5, max: 2, step: 0.5 } }));
    expect(cfg?.options.map((o) => o.value)).toEqual(['0.5', '1', '1.5', '2']);
  });

  it('没写 type 时按 options / min 推断，旧声明不至于渲染成文本框', () => {
    const [ratio, steps] = paramControlsFor(
      model({ aspectRatio: { options: ['1:1'] }, steps: { min: 1, max: 2 } }),
    );
    expect(ratio?.kind).toBe('enum');
    expect(steps?.kind).toBe('int');
  });

  it('bool 与 string 各自渲染成开关与输入框', () => {
    const controls = paramControlsFor(
      model({ watermark: { type: 'bool', default: true }, negativePrompt: { type: 'string' } }),
    );
    expect(controls[0]?.kind).toBe('bool');
    expect(controls[0]?.defaultValue).toBe(true);
    expect(controls[1]?.kind).toBe('string');
    expect(controls[1]?.defaultValue).toBe('');
  });

  it('schema 自带 label / description 时优先用它', () => {
    const [control] = paramControlsFor(
      model({ quality: { type: 'enum', options: ['high'], label: '画质档位', description: '越高越慢' } }),
    );
    expect(control?.label).toBe('画质档位');
    expect(control?.hint).toBe('越高越慢');
  });

  it('未知字段名用 key 原文兜底，仍然渲染（不静默丢弃）', () => {
    const [control] = paramControlsFor(model({ weirdFlag: { type: 'bool' } }));
    expect(control?.label).toBe('weirdFlag');
  });

  it('没有模型时给空列表：页面据此不渲染参数区', () => {
    expect(paramControlsFor(undefined)).toEqual([]);
  });
});

describe('resolveParamValues · 换模型后旧值不能带过去', () => {
  const agnes = paramControlsFor(
    model({
      size: { type: 'enum', options: ['1024x1024'], default: '1024x1024' },
      count: { type: 'int', min: 1, max: 1, default: 1 },
    }),
  );
  const seedream = paramControlsFor(
    model({
      size: { type: 'enum', options: ['1024x1024'], default: '1024x1024' },
      count: { type: 'int', min: 1, max: 4, default: 1 },
    }),
  );

  it('不改动时全部取 schema 默认值', () => {
    expect(initialParamValues(agnes)).toEqual({ size: '1024x1024', count: 1 });
    expect(resolveParamValues(agnes, {})).toEqual({ size: '1024x1024', count: 1 });
  });

  it('档位失效回落默认值（seedream 选 4 → 切到 agnes 只有 1）', () => {
    const values = resolveParamValues(agnes, { count: 4 });
    expect(values.count).toBe(1);
  });

  it('类型对不上回落默认值（避免把字符串张数发给后端）', () => {
    expect(resolveParamValues(seedream, { count: '3' }).count).toBe(1);
  });

  it('当前模型没有的键被丢弃，不会混进提交参数', () => {
    const values = resolveParamValues(agnes, { quality: 'high' });
    expect(values).not.toHaveProperty('quality');
  });
});

describe('输入辅助', () => {
  it('空串与纯空白算空值，不提交', () => {
    expect(isEmptyParamValue('')).toBe(true);
    expect(isEmptyParamValue(undefined)).toBe(true);
    expect(isEmptyParamValue(0)).toBe(false);
    expect(isEmptyParamValue(false)).toBe(false);
  });

  it('数字输入框输入非数字时保持空串，而不是 NaN', () => {
    expect(parseNumericInput('12')).toBe(12);
    expect(parseNumericInput('')).toBe('');
    expect(parseNumericInput('abc')).toBe('');
  });
});
