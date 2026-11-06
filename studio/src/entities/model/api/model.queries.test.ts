import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  FALLBACK_MODELS,
  aspectOptionsFor,
  durationOptionsFor,
  imageFormSchemaFor,
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

describe('imageFormSchemaFor（生图表单参数来自 paramSchema）', () => {
  function imageModel(countMax: number, overrides: Partial<ModelSpec> = {}): ModelSpec {
    return spec({
      capability: 'image',
      paramSchema: {
        size: {
          type: 'enum',
          options: ['1024x1024', '1024x1536', '1536x1024'],
          default: '1024x1024',
        },
        count: { type: 'int', min: 1, max: countMax, default: 1 },
      },
      ...overrides,
    });
  }

  it('尺寸取 enum 白名单并带半角 x，张数按 min..max 逐档展开', () => {
    expect(imageFormSchemaFor(imageModel(1))).toEqual({
      sizeOptions: ['1024x1024', '1024x1536', '1536x1024'],
      sizeDefault: '1024x1024',
      countOptions: [1],
      countDefault: 1,
    });
    // 真实后端 agnes max=1、seedream max=4：上限是按模型变的，不能写死
    expect(imageFormSchemaFor(imageModel(4)).countOptions).toEqual([1, 2, 3, 4]);
  });

  it('default 不在合法档位里时回落到第一个选项', () => {
    const model = imageModel(2, {
      paramSchema: {
        size: { type: 'enum', options: ['512x512', '1024x1024'], default: '2048x2048' },
        count: { type: 'int', min: 1, max: 2, default: 9 },
      },
    });
    const form = imageFormSchemaFor(model);
    expect(form.sizeDefault).toBe('512x512');
    expect(form.countDefault).toBe(1);
  });

  it('没有模型时给空档位，不编造尺寸 / 张数默认值', () => {
    expect(imageFormSchemaFor(undefined)).toEqual({
      sizeOptions: [],
      sizeDefault: '',
      countOptions: [],
      countDefault: 0,
    });
  });

  it('供应商给出非法 min/max 时也不会出现「0 张」选项', () => {
    const model = imageModel(1, {
      paramSchema: { count: { type: 'int', min: 0, max: 0, step: 0 } },
    });
    const form = imageFormSchemaFor(model);
    expect(form.countOptions.length).toBeGreaterThan(0);
    expect(form.countOptions.every((v) => v >= 1)).toBe(true);
  });

  it('生图页不再写死全角 × 尺寸或固定张数（防回退）', () => {
    const source = readFileSync(join(process.cwd(), 'src/pages/image/ImagePage.tsx'), 'utf8');
    expect(source).not.toContain('×');
    expect(source).not.toMatch(/SIZE_OPTIONS|COUNT_OPTIONS/);
  });
});
