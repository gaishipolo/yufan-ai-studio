import { describe, expect, it } from 'vitest';
import type { SpecSlotIn, TemplateSpecIn } from '@/shared/api/schema';
import { publishPreflight } from './promptDocument';

function spec(
  slots: SpecSlotIn[],
  prompt: string,
  randomPrompt = '',
): TemplateSpecIn {
  return { key: 'k', name: '模板', model: 'm', prompt, randomPrompt, slots };
}

function slot(overrides: Partial<SpecSlotIn> = {}): SpecSlotIn {
  return {
    key: 'hero',
    type: 'image',
    binding: 'user',
    refMode: 'mixed',
    minCount: 1,
    maxCount: 1,
    label: '主图',
    tagIds: ['tag-1'],
    ...overrides,
  };
}

describe('publishPreflight', () => {
  it('先拦没圈候选标签的槽位', () => {
    const error = publishPreflight(spec([slot({ tagIds: [] })], '{{slot:hero}}'));

    expect(error).toContain('还没圈候选标签');
    expect(error).toContain('主图');
  });

  it('必填槽位正文没引用时提前点名', () => {
    const error = publishPreflight(spec([slot()], '一段没有引用的提示词'));

    expect(error).toContain('必填');
    expect(error).toContain('正文里没有引用');
  });

  it('可选槽位在框选段之外被引用时提前拦截', () => {
    const error = publishPreflight(
      spec([slot({ minCount: 0 })], '{{slot:hero}} 站在雪原上'),
    );

    expect(error).toContain('可选槽位');
    expect(error).toContain('框选');
  });

  it('随机档里同一可选槽位出现两次时提前拦截', () => {
    const error = publishPreflight(
      spec(
        [slot({ minCount: 0, emptyText: '雪原', emptyTextFromAnchor: true })],
        '⟦e:hero⟧雪原⟦/e⟧',
        '{{slot:hero}} 和 {{slot:hero}}',
      ),
    );

    expect(error).toContain('最多引用一次');
  });

  it('合法模板返回 null', () => {
    expect(
      publishPreflight(
        spec(
          [slot({ minCount: 0, emptyText: '雪原', emptyTextFromAnchor: true })],
          '⟦e:hero⟧雪原⟦/e⟧',
        ),
      ),
    ).toBeNull();
  });
});