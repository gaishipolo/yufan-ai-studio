import { describe, expect, it } from 'vitest';
import type { TemplateCaseOut } from '@/shared/api/schema';
import {
  CASE_STATUS_LABELS,
  formatTime,
  reuseSpecName,
  snapshotModel,
  snapshotPrompt,
  snapshotSlotCount,
} from './caseMeta';

function makeCase(overrides: Partial<TemplateCaseOut> = {}): TemplateCaseOut {
  return {
    id: 'case_1',
    name: '人物替换示例',
    description: '一段描述',
    category: '人物替换',
    status: 'published',
    specId: 'spec_1',
    specKey: 'spec_abc',
    specName: '人物替换模板',
    specVersion: 3,
    sourceMissing: false,
    config: {},
    ownerId: 'u_1',
    ownerName: '张三',
    publishedAt: '2026-01-02T03:04:05Z',
    offlineAt: null,
    createdAt: '2026-01-02T03:04:05Z',
    updatedAt: '2026-01-02T03:04:05Z',
    ...overrides,
  };
}

describe('案例展示口径', () => {
  it('状态词只有一个出处，别处直接抄就会漂', () => {
    expect(CASE_STATUS_LABELS.published).toBe('已发布');
    expect(CASE_STATUS_LABELS.offline).toBe('已下架');
  });

  it('复用后的模板名带「副本」：模板名在同一作者名下不能重复', () => {
    expect(reuseSpecName(makeCase())).toBe('人物替换模板 副本');
  });

  it('源模板没有名字就退回用 key', () => {
    expect(reuseSpecName(makeCase({specName: ''}))).toBe('spec_abc 副本');
  });

  it('快照里的槽位数、模型、提示词都能读出来', () => {
    const config = {
      slots: [{key: 'a'}, {key: 'b'}],
      model: 'model_x',
      prompt: '一张图，暖色调',
    };
    expect(snapshotSlotCount(config)).toBe(2);
    expect(snapshotModel(config)).toBe('model_x');
    expect(snapshotPrompt(config)).toBe('一张图，暖色调');
  });

  it('快照是空的或字段不对时给的是空值，不是崩溃', () => {
    expect(snapshotSlotCount(undefined)).toBe(0);
    expect(snapshotSlotCount({slots: 'not-a-list'})).toBe(0);
    expect(snapshotModel({model: 3 as unknown as string})).toBe('');
    expect(snapshotPrompt({})).toBe('');
  });

  it('时间缺失给占位符，不给「Invalid Date」', () => {
    expect(formatTime(null)).toBe('—');
    expect(formatTime('不是时间')).toBe('—');
    expect(formatTime('2026-01-02T03:04:05Z')).not.toBe('—');
  });
});
