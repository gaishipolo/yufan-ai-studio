import { describe, expect, it } from 'vitest';
import type { SpecCompileOut } from '@/shared/api/schema';
import type { ParamField } from '@/entities/model/model/types';
import { toImagePayload, toSubmitPayload, toTaskPayload } from './taskPayload';

/**
 * `compile` → 提交接口的字段摊平。
 *
 * 四件事必须钉死：
 * 1. 参数不许静默丢（其余声明的参数进 `params`）；
 * 2. 模型没声明的键**当场报错**（而不是丢给后端才 400）；
 * 3. 时长不必有来源（模型不声明 `durationSec` 时就不传，别编一个 5 秒）；
 * 4. **图片模板不许走 /tasks**（那边只收视频能力，会把 `count` 当非法参数拒掉，
 *    用户只看到一句跟模型无关的报错）。
 */

/** LibTV 的真实声明（`drivers/libtv.py` 的 `_PARAM_SCHEMA`）。 */
const LIBTV_SCHEMA: Record<string, ParamField> = {
  durationSec: { type: 'int', min: 2, max: 30, step: 1, default: 5 },
  aspectRatio: { type: 'enum', options: ['9:16', '16:9', '1:1'], default: '9:16' },
  billingMode: {
    type: 'enum',
    options: ['free', 'credits'],
    default: 'free',
    label: '计费模式',
    userSelectable: true,
  },
};

const AGNES_IMAGE_SCHEMA: Record<string, ParamField> = {
  size: { type: 'enum', options: ['1024x1024'], default: '1024x1024' },
  count: { type: 'int', min: 1, max: 1, default: 1 },
};

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

  it('模型压根不声明时长时就不传，别编一个 5 秒覆盖它的出片时长', () => {
    // 有些视频模型有自己的时长（不由 paramSchema 决定），编一个 5 秒过去
    // 反而覆盖了它 —— 用户没选过时长，却拿到了一条「5 秒」的请求。
    const result = toTaskPayload(compiled({params: {}}), 't');
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect('durationSec' in result.payload).toBe(false);
  });

  it('模型没声明的键当场报错，而不是丢给后端才 400', () => {
    const result = toTaskPayload(
      compiled({params: {durationSec: 5, szie: '1024x1536'}}),
      't',
      {},
      {paramSchema: LIBTV_SCHEMA},
    );
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.message).toContain('szie');
    // 报错要给出可选项：用户改错一个键，得知道该填什么
    expect(result.message).toContain('billingMode');
  });

  it('模型声明了、顶层没有的键进自由 params，不被丢掉', () => {
    // `quality` 这类参数没有顶层字段，但模型声明了 → 进 `params`，
    // 由服务端按 schema 校验。丢掉的话用户就拿到一个「参数没用上」的任务。
    const result = toTaskPayload(
      compiled({params: {durationSec: 6, quality: 'hd'}}),
      't',
      {},
      {paramSchema: {...LIBTV_SCHEMA, quality: {type: 'enum', options: ['hd', 'sd']}}},
    );
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.payload.params).toEqual({quality: 'hd'});
  });

  it('没传 schema 时不做预检，全部交给后端按模型 schema 判', () => {
    // 调用方拿不到模型目录时不该瞎猜「这个键不认」—— 后端那道校验才是权威的。
    const result = toTaskPayload(compiled({params: {durationSec: 6, quality: 'hd'}}), 't');
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.payload.params).toEqual({quality: 'hd'});
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

  it('billingMode 摊到顶层，不被当成「无法提交」的参数', () => {
    // 计费模式是 /tasks 的顶层字段（CreateTaskIn.billingMode），不是自由 params。
    // 漏掉它 → 模板库提交时被判「这些参数无法提交」，用户填的积分加速到不了后端。
    const result = toTaskPayload(
      compiled({params: {durationSec: 6, aspectRatio: '9:16', billingMode: 'credits'}}),
      't',
      {},
      {paramSchema: LIBTV_SCHEMA},
    );
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.payload.billingMode).toBe('credits');
    // 顶层键不进自由 params：两份真相会让首提与重试读到不同的值
    expect(result.payload.params).toBeUndefined();
  });

  it('模板没声明计费模式时就不占这个字段', () => {
    const result = toTaskPayload(compiled(), 't');
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect('billingMode' in result.payload).toBe(false);
  });
});

describe('toImagePayload', () => {
  it('尺寸与张数进顶层，其余声明的参数进 params', () => {
    const result = toImagePayload(
      compiled({model: 'agnes-image-2.5-flash', params: {size: '1024x1536', count: 2, quality: 'hd'}}),
      {},
      {paramSchema: {...AGNES_IMAGE_SCHEMA, quality: {type: 'enum', options: ['hd', 'sd']}}},
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
      {},
      {paramSchema: AGNES_IMAGE_SCHEMA},
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
    // 生图模型声明了 `count`，视频模型没声明 → 提交前就能发现「这个模板
    // 走错接口了」。等后端 400 的话，用户看到的是一句跟模型无关的报错。
    const result = toSubmitPayload(
      compiled({model: 'agnes-image-2.5-flash', params: {count: 1}}),
      't',
      'video',
      {},
      {paramSchema: LIBTV_SCHEMA},
    );
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.message).toContain('count');
  });
});
