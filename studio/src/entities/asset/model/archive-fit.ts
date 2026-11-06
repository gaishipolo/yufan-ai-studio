import type { ArchivedAsset } from './archive.types';

/**
 * 生成适用性的结论。不适用时带一个可以直接展示给用户的原因。
 *
 * 归档阶段只校验"是不是合法媒体、分类对不对"，1–15 秒与音轨是模板准备阶段的门禁。
 * 这里把同一套条件提前到页面上说明，但**不拦截归档**——合法 MP4 仍然允许存进来，
 * 否则用户手里只有长片时连归档都做不到。
 */
export type ReplacementFitness = { ok: true } | { ok: false; reason: string };

export function replacementFitness(asset: ArchivedAsset): ReplacementFitness {
  if (asset.category !== 'source') return {ok: false, reason: '仅原片参与替换'};
  if (asset.type !== 'video') return {ok: false, reason: '替换需要视频原片'};
  if (asset.hasAudio !== true) {
    return {ok: false, reason: asset.hasAudio === false ? '缺少音轨' : '音轨信息缺失，需重新探测'};
  }
  /* 探测失败与「确实很短」是两件事：说成「时长不在 1–15 秒」会让人去剪一段本来不长的片子。 */
  if (asset.duration == null) return {ok: false, reason: '时长未知，需重新探测'};
  if (asset.duration < 1 || asset.duration > 15) return {ok: false, reason: '时长不在 1–15 秒'};
  return {ok: true};
}
