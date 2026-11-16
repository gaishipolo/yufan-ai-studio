import type { ModelSpec } from '@/entities/model/model/types';
import { effectiveModelIds } from '@/features/template-specs/specDraft';
import type { PayloadDefaults, SubmitKind } from './taskPayload';

/**
 * 模板库这一侧「用哪款模型」的判断。
 *
 * 作者可以在封装时声明「允许选用的模型」：使用模板的人从这份清单里挑，而不是
 * 只能用一个写死的模型。清单为空 = 只有固定模型（老模板的行为，页面上连
 * 下拉都不出现）。
 *
 * 拆成纯函数是因为它有三处要跟着变：**提交到哪个接口**（模型能力决定）、
 * **参数默认值**（按选中的模型取）、**参数控件**（按选中的模型的 schema 渲染）。
 * 写成组件里的三行 if，改一处忘了另一处就会变成「用 A 模型的参数去提交 B 模型」。
 */
export interface ModelChoice {
  /** 实际使用的模型 id（空 = 目录里没有这款模型） */
  id: string;
  /** 提交到哪个接口：图片模型走生图，视频模型走任务 */
  kind: SubmitKind;
  /** 模型目录里的那一条（目录里没有就是 undefined） */
  model?: ModelSpec;
  /** 提交时的参数默认值（按选中的模型取） */
  defaults: PayloadDefaults;
}

/** 模型目录里的一款（视频 + 生图都算）。 */
function catalogOf(
  imageModels: ModelSpec[],
  videoModels: ModelSpec[],
): ModelSpec[] {
  return [...videoModels, ...imageModels];
}

/**
 * 选中模型 → 接口、默认值、目录条目。
 *
 * 选了不在可选项里的模型时退回第一项（固定模型）：宁可给一个能跑的选择，
 * 也不要让界面停在「选了个不存在的模型」上。
 */
export function resolveModelChoice(
  fixedModel: string,
  allowedModels: string[],
  chosen: string,
  imageModels: ModelSpec[],
  videoModels: ModelSpec[],
): ModelChoice {
  const options = effectiveModelIds(fixedModel, allowedModels);
  const id = options.includes(chosen) ? chosen : options[0] ?? '';
  const image = imageModels.find(item => item.id === id);
  const video = videoModels.find(item => item.id === id);
  const model = image ?? video;
  const kind: SubmitKind = image ? 'image' : 'video';
  const defaults: PayloadDefaults =
    kind === 'image'
      ? {
          size: String(image?.paramSchema?.size?.default ?? '') || undefined,
          count: Number(image?.paramSchema?.count?.default ?? 0) || undefined,
        }
      : {
          durationSec: Number(video?.paramSchema?.durationSec?.default ?? 0) || undefined,
          aspectRatio: (video?.paramSchema?.aspectRatio?.default as string | undefined) ?? undefined,
        };
  return {id, kind, model, defaults};
}

/** 下拉里的选项：模型 id + 目录里的中文名（目录里没有就退回 id）。 */
export function modelChoiceOptions(
  ids: string[],
  imageModels: ModelSpec[],
  videoModels: ModelSpec[],
): {value: string; label: string}[] {
  const catalog = catalogOf(imageModels, videoModels);
  return ids.map(id => ({
    value: id,
    label: catalog.find(item => item.id === id)?.label ?? id,
  }));
}
