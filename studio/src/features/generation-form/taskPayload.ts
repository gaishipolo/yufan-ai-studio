import type { GenerateImagePayload } from '@/entities/image/model/types';
import type { CreateTaskPayload } from '@/entities/task/model/types';
import type { ParamField } from '@/entities/model/model/types';
import {
  AspectRatio_VALUES,
  type AspectRatio,
  type SpecCompileOut,
} from '@/shared/api/schema';

/**
 * `compile` → 提交接口的字段摊平。
 *
 * **模板本身不区分图片/视频**，它只声明模型；落到哪个接口由模型能力决定：
 * `Capability.VIDEO` → `POST /tasks`，`Capability.IMAGE` → `POST /generations/image`。
 * 两个接口的入参形状不同（视频有时长/画幅，图片有尺寸/张数），所以这里分成两条，
 * 别指望一个 `params` 直通两边。
 *
 * ## 三条纪律
 *
 * 1. **参数不许静默丢**。视频那边**有**自由 `params` 通道了（`CreateTaskIn.params`），
 *    其余键进那里，由服务端按模型 `paramSchema` 校验 —— 未声明即 400，是可见的
 *    失败，不是悄悄换成默认值。
 * 2. **模型不认的键不许提交**。前端拿得到模型的 `paramSchema`，所以在提交前
 *    就能判定「这个键模型根本没声明」。后端会拒，但那时用户已经填完了一堆
 *    素材 —— 早一步报出来，省掉「为什么我选的东西不能提交」。
 * 3. **顶层键与自由参数不重叠**。`durationSec` / `aspectRatio` / `stylePreset` /
 *    `billingMode` 在接口上有各自的字段；同一个值出现在两处时语义没法确定
 *    （首提读顶层、重试读 params，同一条任务两次用的时长可能不一样）。
 */

/* 这几个键在接口上有**各自的顶层字段**，不经过自由 `params`。 */
const VIDEO_TOP_LEVEL = ['aspectRatio', 'durationSec', 'stylePreset', 'billingMode'] as const;

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

/**
 * 模型的参数声明。**没有它就没法判定「模型认不认这个键」** —— 缺省成空对象时
 * 退化成「不预检，全部交给后端判」（后端仍会拒，但错误信息晚一步）。
 */
export interface PayloadSchema {
  paramSchema?: Record<string, ParamField>;
}

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

/** 参数值：只允许标量（后端按 schema 收敛成 string / number / bool）。 */
type ParamScalar = string | number | boolean;

function isScalar(value: unknown): value is ParamScalar {
  return typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean';
}

/**
 * 只把**模型声明过**的标量带出去。
 *
 * 两条过滤各管一件事：
 * - 顶层键不进 `params`（纪律 3）；
 * - 模型没声明的键**直接报错**（纪律 2）—— 静默丢掉的话，用户改的那个值
 *   从来没生效过，页面看起来却一切正常。
 *
 * 没有 schema 时（调用方没给）不预检：全量透传，交给后端按模型 schema 判。
 * 后端那道校验是权威的，前端这道只是为了早一步报出来。
 */
function collectParams(
  params: Record<string, unknown>,
  topLevel: readonly string[],
  schema: Record<string, ParamField> | undefined,
  modelLabel: string,
): { ok: true; params: Record<string, ParamScalar> } | { ok: false; message: string } {
  const extra: Record<string, ParamScalar> = {};
  const unknown: string[] = [];

  for (const [key, value] of Object.entries(params)) {
    if (topLevel.includes(key)) continue;
    if (!isScalar(value)) continue;
    if (schema && !(key in schema)) {
      unknown.push(key);
      continue;
    }
    extra[key] = value;
  }

  if (unknown.length > 0) {
    const available = schema ? Object.keys(schema).sort() : [];
    return {
      ok: false,
      message:
        `模型 ${modelLabel} 不接受参数 ${unknown.sort().join('、')}` +
        (available.length ? `，可用：${available.join('、')}` : ''),
    };
  }
  return { ok: true, params: extra };
}

/** 模型的 `paramSchema`：调用方没传就当「不知道」，不预检。 */
function schemaOf(source: PayloadSchema | undefined): Record<string, ParamField> | undefined {
  const declared = source?.paramSchema;
  if (!declared || typeof declared !== 'object') return undefined;
  return declared;
}

function isAspectRatio(value: string): value is AspectRatio {
  return AspectRatio_VALUES.some((ratio) => ratio === value);
}

export function toTaskPayload(
  compiled: SpecCompileOut,
  title: string,
  defaults: PayloadDefaults = {},
  schema?: PayloadSchema,
): TaskPayloadResult {
  const params = (compiled.params ?? {}) as Record<string, unknown>;
  const declared = schemaOf(schema);

  /* 时长：模板声明了就用模板的，模板没声明就用模型目录里的默认档位。
     两处都没有就**不传** —— 有些视频模型压根不声明 `durationSec`，
     那种模型有自己的出片时长，编一个 5 秒过去反而覆盖了它。 */
  const rawDuration = params.durationSec ?? defaults.durationSec;
  const duration = Number(rawDuration ?? 0);
  if (rawDuration !== undefined && (!Number.isFinite(duration) || duration <= 0)) {
    return { ok: false, message: `时长不合法：${String(rawDuration)}` };
  }

  const collected = collectParams(
    params,
    VIDEO_TOP_LEVEL,
    declared,
    compiled.model,
  );
  if (!collected.ok) return collected;

  const aspectRatio = String(params.aspectRatio ?? defaults.aspectRatio ?? '9:16');
  if (!isAspectRatio(aspectRatio)) {
    return { ok: false, message: `画幅不合法：${aspectRatio}` };
  }

  return {
    ok: true,
    payload: {
      title,
      prompt: compiled.prompt,
      model: compiled.model,
      aspectRatio,
      ...(duration > 0 ? { durationSec: duration } : {}),
      referenceLibraryIds: compiled.referenceLibraryIds ?? [],
      textLibraryIds: compiled.textLibraryIds ?? [],
      ...(params.stylePreset ? { stylePreset: String(params.stylePreset) } : {}),
      ...(params.billingMode ? { billingMode: String(params.billingMode) } : {}),
      ...(Object.keys(collected.params).length > 0 ? { params: collected.params } : {}),
    },
  };
}

export function toImagePayload(
  compiled: SpecCompileOut,
  title: string,
  defaults: PayloadDefaults = {},
  schema?: PayloadSchema,
): ImagePayloadResult {
  const params = (compiled.params ?? {}) as Record<string, unknown>;
  const declared = schemaOf(schema);
  const size = String(params.size ?? defaults.size ?? '').trim();
  const rawCount = Number(params.count ?? defaults.count ?? 0);
  if (!Number.isFinite(rawCount) || rawCount < 0) {
    return { ok: false, message: `张数不合法：${String(params.count)}` };
  }

  const collected = collectParams(params, IMAGE_TOP_LEVEL, declared, compiled.model);
  if (!collected.ok) return collected;

  return {
    ok: true,
    payload: {
      /* 任务名与视频那条对齐：模板库传模板名，后端用它当生图历史 / 任务中心的卡片名。
         空字符串不占字段，直接生图那条路仍由后端从提示词派生。 */
      ...(title.trim() ? { name: title } : {}),
      prompt: compiled.prompt,
      model: compiled.model,
      ...(size ? { size } : {}),
      count: Math.round(rawCount),
      referenceLibraryIds: compiled.referenceLibraryIds ?? [],
      textLibraryIds: compiled.textLibraryIds ?? [],
      ...(Object.keys(collected.params).length > 0 ? { params: collected.params } : {}),
    },
  };
}

/** 按模型能力选接口。`kind` 由调用方从模型目录判定，这里只负责形状。 */
export function toSubmitPayload(
  compiled: SpecCompileOut,
  title: string,
  kind: SubmitKind,
  defaults: PayloadDefaults = {},
  schema?: PayloadSchema,
): SubmitResult {
  if (kind === 'image') {
    const image = toImagePayload(compiled, title, defaults, schema);
    return image.ok ? { ok: true, kind: 'image', payload: image.payload } : image;
  }
  const video = toTaskPayload(compiled, title, defaults, schema);
  return video.ok ? { ok: true, kind: 'video', payload: video.payload } : video;
}
