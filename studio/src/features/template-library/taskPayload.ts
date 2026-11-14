import type { CreateTaskPayload } from '@/entities/task/model/types';
import type { AspectRatio, SpecCompileOut } from '@/shared/api/schema';

/**
 * `compile` → `POST /tasks` 的字段摊平。
 *
 * 两件事必须在这里做对，否则用户填的东西会静默消失：
 *
 * 1. **`params` 只认这三个顶层键**（`aspectRatio` / `durationSec` / `stylePreset`）。
 *    `/tasks` 没有自由 `params`，多出来的键（生图模型的 `size` / `count`）到那里没有落点，
 *    所以宁可报错也不丢 —— 丢掉等于按模型默认值跑，而模板里明明写了。
 * 2. **时长要有来源**：模板 `params` 没声明就取模型 `paramSchema.durationSec.default`；
 *    两处都没有就报错，不编一个 5 秒出来。
 *
 * 映射只有这一份：页面不自己拼字段，测试直接断言输出能通过 `CreateTaskPayload` 的类型。
 */
const TOP_LEVEL = ['aspectRatio', 'durationSec', 'stylePreset'] as const;
type TopLevel = (typeof TOP_LEVEL)[number];

export interface PayloadDefaults {
  /** 模型的默认画幅（`paramSchema.aspectRatio.default`） */
  aspectRatio?: string;
  /** 模型的默认时长（`paramSchema.durationSec.default`） */
  durationSec?: number;
}

export type PayloadResult =
  | { ok: true; payload: CreateTaskPayload }
  | { ok: false; message: string };

export function toTaskPayload(
  compiled: SpecCompileOut,
  title: string,
  defaults: PayloadDefaults = {},
): PayloadResult {
  const params = (compiled.params ?? {}) as Record<string, unknown>;

  const unknown = Object.keys(params).filter(key => !TOP_LEVEL.includes(key as TopLevel));
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
