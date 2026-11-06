import type { ReplacementRun } from './replacement.types';

const KIND_LABEL = {character: '人物替换', product: '产品替换', scene: '场景替换'};
const SOURCE_NAME_LIMIT = 18;

function sourceTitle(run: ReplacementRun): string {
  return replacementSourceName(run).trim().replace(/\.mp4$/i, '') || '未命名原片';
}

export function replacementTaskFullName(run: ReplacementRun): string {
  return `${sourceTitle(run)} · ${KIND_LABEL[run.kind]} · ${run.target} · #${run.id.slice(0, 8).toUpperCase()}`;
}

/**
 * 人物/产品名称是替换目标；调用 ID 才是任务身份，刷新或重试时保持不变。
 * 优先用后端 `displayTitle`：任务中心也读同一字段，两处标题才对得上。
 */
export function replacementTaskName(run: ReplacementRun): string {
  if (run.displayTitle) return run.displayTitle;
  const characters = Array.from(sourceTitle(run));
  const prefix = characters.length > SOURCE_NAME_LIMIT ? `${characters.slice(0, SOURCE_NAME_LIMIT).join('')}…` : characters.join('');
  return `${prefix} · ${KIND_LABEL[run.kind]} · ${run.target} · #${run.id.slice(0, 8).toUpperCase()}`;
}

export function replacementSourceName(run: ReplacementRun): string {
  return run.inputSummary?.assets?.find(asset => asset.role === 'source_video')?.name ?? '历史档案未记录原片';
}
