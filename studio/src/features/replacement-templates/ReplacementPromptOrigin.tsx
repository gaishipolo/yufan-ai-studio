import type { ReplacementRun } from '@/entities/template/model/replacement.types';

/**
 * 「这条提示词是谁写的」——两种提示词在文本上分不出来，必须由档案自报来源。
 *
 * 封装视频模板时写死的提示词是**逐字提交**的：用户要能看到这句话，才敢相信
 * 面板里跑的不是系统另外编的一份。反过来，自动编译出来的也要说清楚，
 * 免得用户去模板库里找一条并不存在的模板。
 */
export function ReplacementPromptOrigin({run}: {run: ReplacementRun}) {
  if (run.promptMode !== 'fixed') {
    return <p className="rounded-lg bg-panel-soft p-3 text-xs text-ink-soft">
      来源：按本次素材与替换范围自动编译，未套用封装模板。
    </p>;
  }
  const name = run.videoTemplateName?.trim();
  if (run.inputSummary?.scenePrompt) return <p className="rounded-lg bg-primary/5 p-3 text-xs text-ink-soft">
    来源：{name ? `视频模板「${name}」` : '已封装的视频模板'}，已填入本次场景描述；其余固定约束保持原文。
  </p>;
  return <p className="rounded-lg bg-primary/5 p-3 text-xs text-ink-soft">
    来源：{name ? `视频模板「${name}」` : '已封装的视频模板'}的固定提示词，逐字提交，未改写。
  </p>;
}
