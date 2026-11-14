import type { GenerateImagePayload } from '@/entities/image/model/types';
import type { CreateTaskPayload } from '@/entities/task/model/types';
import type { AspectRatio, SpecCompileOut } from '@/shared/api/schema';

/**
 * `compile` → 提交接口的字段摊平。
 *
 * **模板本身不区分图片/视频**，它只声明模型；落到哪个接口由模型能力决定：
 * `Capability.VIDEO` → `POST /tasks`，`Capability.IMAGE` → `POST /generations/image`。
 * 两个接口的入参形状不同（视频有时长/画幅，图片有尺寸/张数），所以这里分成两条，
 * 别指望一个 `params` 直通两边。
 *
 * 两条都守同一件事：**参数不许静默丢**。视频那边多出来的键没有落点，宁可报错；
 * 图片那边除 `size` / `count` 之外的键进 `params`，由服务端按模型 `paramSchema`
 * 校验（未声明即 400）—— 也是可见的失败，不是悄悄换成默认值。
 */
const VIDEO_TOP_LEVEL = ['aspectRatio', 'durationSec', 'stylePreset'] as const;
type VideoTopLevel = (typeof VIDEO_TOP_LEVEL)[number];

/** 图片接口的顶层键：其余声明的参数进 `params`。 */
const IMAGE_TOP_LEVEL = ['size', 'count'] as const;

export interface PayloadDefaults {
  /** 模型的默认画幅（`paramSchema.aspectRatio.default`） */
  aspectRatio?: string;
  /** 模型的默认时长（`paramSchema.durationSec.default`） */
  durationSec?: number;
  /** 生图模型的默认尺寸（`paramSchema.size.default`），空 = 用服务端默认 */
  size?: string;
  /** 生图模型的默认张数（`paramSchema.count.default`），0 = 用服务端默认 */
  count?: number;
}

/** 提交目标：由模型能力决定，不由模板声明。 */
export type SubmitKind = 'video' | 'image';

export type TaskPayloadResult =
  | { ok: true; payload: CreateTaskPayload }
  | { ok: false; message: string };

export type ImagePayloadResult =
  | { ok: true; payload: GenerateImagePayload }
  | { ok: false; message: string };

export type SubmitResult =
  | { ok: true; kind: 'video'; payload: CreateTaskPayload }
  | { ok: true; kind: 'image'; payload: GenerateImagePayload }
  | { ok: false; message: string };

export function toTaskPayload(
  compiled: SpecCompileOut,
  title: string,
  defaults: PayloadDefaults = {},
): TaskPayloadResult {
  const params = (compiled.params ?? {}) as Record<string, unknown>;

  const unknown = Object.keys(params).filter(
    key => !VIDEO_TOP_LEVEL.includes(key as VideoTopLevel),
  );
  if (unknown.length > 0) {
    return {
      ok: false,
      message: `这些参数无法提交给通用生成任务：${[...unknown].sort().join('、')}`,
    };
  }

  const duration = Number(params.durationSec ?? defaults.durationSec ?? 0);
  if (!Number.isFinite(duration) || duration <= 0) {
    return { ok: false, message: '模板没有声明时长，模型目录里也没有默认档位' };
  }

  return {
    ok: true,
    payload: {
      title,
      prompt: compiled.prompt,
      model: compiled.model,
      aspectRatio: String(params.aspectRatio ?? defaults.aspectRatio ?? '9:16') as AspectRatio,
      durationSec: duration,
      referenceLibraryIds: compiled.referenceLibraryIds ?? [],
      textLibraryIds: compiled.textLibraryIds ?? [],
      ...(params.stylePreset ? { stylePreset: String(params.stylePreset) } : {}),
    },
  };
}

export function toImagePayload(
  compiled: SpecCompileOut,
  defaults: PayloadDefaults = {},
): ImagePayloadResult {
  const params = (compiled.params ?? {}) as Record<string, unknown>;
  const size = String(params.size ?? defaults.size ?? '').trim();
  const rawCount = Number(params.count ?? defaults.count ?? 0);
  if (!Number.isFinite(rawCount) || rawCount < 0) {
    return { ok: false, message: `张数不合法：${String(params.count)}` };
  }

  /* 除尺寸与张数之外声明的参数进 `params`：它们没有顶层落点，但**不能丢**——
     丢了用户就会拿到一张「参数其实没用上」的图，界面上还显示成功。 */
  const extra = Object.fromEntries(
    Object.entries(params).filter(([key]) => !IMAGE_TOP_LEVEL.includes(key as 'size' | 'count')),
  );

  return {
    ok: true,
    payload: {
      prompt: compiled.prompt,
      model: compiled.model,
      ...(size ? { size } : {}),
      count: Math.round(rawCount),
      referenceLibraryIds: compiled.referenceLibraryIds ?? [],
      textLibraryIds: compiled.textLibraryIds ?? [],
      ...(Object.keys(extra).length > 0 ? { params: extra } : {}),
    },
  };
}

/** 按模型能力选接口。`kind` 由调用方从模型目录判定，这里只负责形状。 */
export function toSubmitPayload(
  compiled: SpecCompileOut,
  title: string,
  kind: SubmitKind,
  defaults: PayloadDefaults = {},
): SubmitResult {
  if (kind === 'image') {
    const image = toImagePayload(compiled, defaults);
    return image.ok ? { ok: true, kind: 'image', payload: image.payload } : image;
  }
  const video = toTaskPayload(compiled, title, defaults);
  return video.ok ? { ok: true, kind: 'video', payload: video.payload } : video;
}
