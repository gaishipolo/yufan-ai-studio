import { describe, expect, it } from 'vitest';
import {
  FALLBACK_MODELS,
  aspectOptionsFor,
  billingModeOptionsFor,
  durationOptionsFor,
  referenceKindsFor,
  referenceLimitFor,
} from './model.queries';
import type { ModelSpec } from '../model/types';

function spec(overrides: Partial<ModelSpec> = {}): ModelSpec {
  return {
    id: 'm1',
    label: 'M1',
    capability: 'video',
    provider: 'echo',
    paramSchema: { durationSec: { type: 'int', min: 5, max: 12, default: 5 } },
    isDefault: true,
    ...overrides,
  };
}

describe('durationOptionsFor', () => {
  it('从 schema 的 min/max/step 生成档位', () => {
    expect(durationOptionsFor(spec())).toEqual([5, 10, 12]);
  });

  it('步长 1 时逐档展开', () => {
    const model = spec({
      paramSchema: { durationSec: { type: 'int', min: 1, max: 3, step: 1 } },
    });
    expect(durationOptionsFor(model)).toEqual([1, 2, 3]);
  });

  it('模型未声明 step 时按 5 秒一档', () => {
    expect(durationOptionsFor(undefined)).toEqual([5, 10, 15]);
  });

  it('上限不会被步长跳过（max=12、step=5 仍能选到 12）', () => {
    const options = durationOptionsFor(spec());
    expect(options[options.length - 1]).toBe(12);
  });

  it('非法 min/max 收敛到可用范围，不产生空选项', () => {
    const model = spec({
      paramSchema: { durationSec: { type: 'int', min: 0, max: 0, step: 0 } },
    });
    const options = durationOptionsFor(model);
    expect(options.length).toBeGreaterThan(0);
    expect(options.every((v) => v >= 1)).toBe(true);
  });
});

describe('aspectOptionsFor', () => {
  it('只给出模型声明的比例，并带可读标签', () => {
    const model = spec({
      paramSchema: { aspectRatio: { type: 'enum', options: ['9:16', '1:1'] } },
    });
    expect(aspectOptionsFor(model)).toEqual([
      { value: '9:16', label: '9:16 竖版' },
      { value: '1:1', label: '1:1 方形' },
    ]);
  });

  it('模型未声明比例时回落到三种常用比例', () => {
    expect(aspectOptionsFor(undefined).map((item) => item.value)).toEqual(['9:16', '16:9', '1:1']);
  });
});

describe('billingModeOptionsFor', () => {
  it('只给出模型声明的计费模式，并带可读标签', () => {
    const model = spec({
      paramSchema: {
        durationSec: { type: 'int', min: 2, max: 30, default: 5 },
        billingMode: { type: 'enum', options: ['free', 'credits'], default: 'free' },
      },
    });
    expect(billingModeOptionsFor(model)).toEqual([
      { value: 'free', label: '免费队列（较慢，不扣积分）' },
      { value: 'credits', label: '积分加速（更快，消耗积分）' },
    ]);
  });

  it('模型未声明计费模式时不给出选项', () => {
    // 未声明的模型只有一条上游队列：后端会拒绝非空的 billingMode，
    // 界面也不该给这个档位。
    expect(billingModeOptionsFor(spec())).toEqual([]);
    expect(billingModeOptionsFor(undefined)).toEqual([]);
  });
});

describe('referenceKindsFor / referenceLimitFor', () => {
  it('按模型声明给出参考素材的类型与数量上限', () => {
    const model = spec({ referenceKinds: ['image', 'video', 'audio'], maxReferences: 5 });
    expect(referenceKindsFor(model)).toEqual(['image', 'video', 'audio']);
    expect(referenceLimitFor(model)).toBe(5);
  });

  it('生图模型只收图片，上限用自己的声明', () => {
    const model = spec({ referenceKinds: ['image'], maxReferences: 4 });
    expect(referenceKindsFor(model)).toEqual(['image']);
    expect(referenceLimitFor(model)).toBe(4);
  });

  it('模型没声明时回落到最保守的一档', () => {
    // 纯文生模型不该画出一个必然被后端拒的参考素材入口
    expect(referenceKindsFor(spec())).toEqual(['image']);
    expect(referenceLimitFor(spec())).toBe(1);
    expect(referenceKindsFor(undefined)).toEqual(['image']);
    expect(referenceLimitFor(undefined)).toBe(1);
  });
});

describe('FALLBACK_MODELS（接口不可用时的最小兜底）', () => {
  it('id 唯一，否则 Select 会渲染重复 key', () => {
    const ids = FALLBACK_MODELS.map((model) => model.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it('有且只有一个默认模型，创建表单才有确定初值', () => {
    expect(FALLBACK_MODELS.filter((model) => model.isDefault)).toHaveLength(1);
  });

  it('每个模型都声明了时长范围，否则选择器只能给出默认档', () => {
    for (const model of FALLBACK_MODELS) {
      const schema = model.paramSchema.durationSec;
      expect(schema?.min, `${model.id} 缺少 min`).toBeTypeOf('number');
      expect(schema?.max, `${model.id} 缺少 max`).toBeTypeOf('number');
      expect(Number(schema?.max)).toBeGreaterThan(Number(schema?.min));
    }
  });
});
