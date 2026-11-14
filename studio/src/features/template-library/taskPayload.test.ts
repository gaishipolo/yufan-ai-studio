import { describe, expect, it } from 'vitest';
import type { SpecCompileOut } from '@/shared/api/schema';
import { toImagePayload, toSubmitPayload, toTaskPayload } from './taskPayload';

/**
 * `compile` → 提交接口的字段摊平。
 *
 * 三件事必须钉死：参数不许静默丢（视频没有自由 `params`，图片的额外参数进 `params`）、
 * 时长必须有来源（不许凭空编一个 5 秒）、**图片模板不许走 /tasks**（那边只收视频能力，
 * 会把 `count` 当非法参数拒掉，用户只看到一句跟模型无关的报错）。
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

describe('toImagePayload', () => {
  it('尺寸与张数进顶层，其余声明的参数进 params', () => {
    const result = toImagePayload(
      compiled({model: 'agnes-image-2.5-flash', params: {size: '1024x1536', count: 2, quality: 'hd'}}),
    );
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.payload).toEqual({
      prompt: '把 {{Mixed 1}} 换成 {{Mixed 2}}',
      model: 'agnes-image-2.5-flash',
      size: '1024x1536',
      count: 2,
      referenceLibraryIds: ['a', 'b'],
      textLibraryIds: [],
      params: {quality: 'hd'},
    });
  });

  it('模板没声明尺寸时不出来空字符串，交给服务端取模型默认', () => {
    const result = toImagePayload(
      compiled({model: 'agnes-image-2.5-flash', params: {count: 1}}),
    );
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect('size' in result.payload).toBe(false);
    expect(result.payload.count).toBe(1);
  });

  it('张数不合法当场报错，不四舍五入成一个奇怪的数', () => {
    const bad = toImagePayload(compiled({params: {count: -2}}));
    expect(bad.ok).toBe(false);
    if (bad.ok) return;
    expect(bad.message).toContain('张数');
  });
});

describe('toSubmitPayload', () => {
  it('图片模型走生图接口，视频模型走任务接口', () => {
    const image = toSubmitPayload(
      compiled({model: 'agnes-image-2.5-flash', params: {count: 1}}),
      '简单测试-图片',
      'image',
    );
    expect(image.ok && image.kind).toBe('image');

    const video = toSubmitPayload(compiled(), '简单测试-视频', 'video');
    expect(video.ok && video.kind).toBe('video');
  });

  it('同一个模板内容，按 video 提交就会因为 count 报错 —— 能力判错必须看得见', () => {
    const result = toSubmitPayload(
      compiled({model: 'agnes-image-2.5-flash', params: {count: 1}}),
      't',
      'video',
    );
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.message).toContain('count');
  });
});
