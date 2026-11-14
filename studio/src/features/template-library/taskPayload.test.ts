import { describe, expect, it } from 'vitest';
import type { SpecCompileOut } from '@/shared/api/schema';
import { toTaskPayload } from './taskPayload';

/**
 * `compile` → `POST /tasks` 的字段摊平。
 *
 * 两件事必须钉死：参数不许静默丢（`/tasks` 没有自由 `params`），时长必须有来源
 * （不许凭空编一个 5 秒）。
 */

function compiled(overrides: Partial<SpecCompileOut> = {}): SpecCompileOut {
  return {
    version: 1,
    model: 'libtv-wan3-replacement',
    prompt: '把 {{Mixed 1}} 换成 {{Mixed 2}}',
    params: {aspectRatio: '9:16', durationSec: 6},
    referenceLibraryIds: ['a', 'b'],
    textLibraryIds: [],
    slots: [],
    ...overrides,
  };
}

describe('toTaskPayload', () => {
  it('把 params 摊到 /tasks 的顶层字段', () => {
    const result = toTaskPayload(compiled(), '米兰之夜 · 6036');
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.payload).toEqual({
      title: '米兰之夜 · 6036',
      prompt: '把 {{Mixed 1}} 换成 {{Mixed 2}}',
      model: 'libtv-wan3-replacement',
      aspectRatio: '9:16',
      durationSec: 6,
      referenceLibraryIds: ['a', 'b'],
      textLibraryIds: [],
    });
  });

  it('模板没声明时长时取模型的默认档位', () => {
    const result = toTaskPayload(compiled({params: {}}), 't', {
      durationSec: 8,
      aspectRatio: '16:9',
    });
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.payload.durationSec).toBe(8);
    expect(result.payload.aspectRatio).toBe('16:9');
  });

  it('两处都没有时长就报错，不编默认值', () => {
    const result = toTaskPayload(compiled({params: {}}), 't');
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.message).toContain('时长');
  });

  it('/tasks 表达不了的参数必须报错而不是丢掉', () => {
    const result = toTaskPayload(compiled({params: {size: '1024x1536', count: 2}}), 't');
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.message).toContain('count');
    expect(result.message).toContain('size');
  });

  it('stylePreset 透传，空的就不占一个字段', () => {
    const withStyle = toTaskPayload(compiled({params: {durationSec: 5, stylePreset: 'film'}}), 't');
    expect(withStyle.ok && withStyle.payload.stylePreset).toBe('film');
    const without = toTaskPayload(compiled(), 't');
    expect(without.ok && 'stylePreset' in without.payload).toBe(false);
  });

  it('参考素材与文本保持编译给出的顺序', () => {
    const result = toTaskPayload(
      compiled({referenceLibraryIds: ['c', 'a', 'b'], textLibraryIds: ['doc']}),
      't',
    );
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.payload.referenceLibraryIds).toEqual(['c', 'a', 'b']);
    expect(result.payload.textLibraryIds).toEqual(['doc']);
  });
});
