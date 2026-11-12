import type { VideoTemplateOut } from '@/shared/api/schema';

export interface PromptMention { start: number; end: number; query: string }

export function promptMediaLabel(item: {token?: string; type: string}) {
  const number = item.token?.match(/\d+/)?.[0] ?? '';
  return `${item.type === 'video' ? '视频' : item.type === 'audio' ? '音频' : '图片'} ${number}`;
}

/** 只识别当前光标前的 @ 查询，不扫描/重写其他正文。 */
export function findPromptMention(prompt: string, start: number, end = start): PromptMention | null {
  const match = /@([^@\s{}，。；：！？、]*)$/.exec(prompt.slice(0, start));
  return match ? {start: match.index, end, query: match[1]} : null;
}

export function insertPromptReference(prompt: string, range: {start: number; end: number}, token: string) {
  return {prompt: prompt.slice(0, range.start) + token + prompt.slice(range.end), caret: range.start + token.length};
}

/** 前端即时反馈；服务端仍负责最终校验。此函数不规范化或修改正文。 */
export function validateTemplatePrompt(prompt: string, template: VideoTemplateOut): string {
  if (!prompt.trim()) return '提示词不能为空';
  if (prompt.length > 20000) return '提示词最多 20000 字';
  const tokens = [...prompt.matchAll(/\{\{([^{}]+)\}\}/g)].map(match => match[1]);
  const remainder = prompt.replace(/\{\{([^{}]+)\}\}/g, '');
  if (remainder.includes('{{') || remainder.includes('}}')) return '引用标记不完整，请检查花括号';
  const textScene = template.rules?.inputMode === 'video_scene_text';
  const fixed = template.rules?.fixedReferences;
  const fixedTokens = new Set(Array.isArray(fixed) ? fixed.map(ref => ref.token) : []);
  const media = template.mediaOrder ?? [];
  const cited: string[] = [];
  for (const token of tokens) {
    if (token === 'ReplacementScope' && !textScene) {
      if (!Array.isArray(template.rules?.scopeOptions) || !template.rules.scopeOptions.length) return '模板未配置范围参数，无法引用 {{ReplacementScope}}';
      continue;
    }
    if (token === 'ScenePrompt' && textScene) continue;
    if (!/^Mixed [1-9]\d*$/.test(token)) return `非法引用标记 {{${token}}}`;
    const full = `{{${token}}}`;
    if (!media.some(item => item.token === full) || (textScene && token !== 'Mixed 1' && !fixedTokens.has(full))) return `引用 ${full} 超出模板媒体范围`;
    cited.push(full);
  }
  if (textScene) return tokens.filter(token => token === 'ScenePrompt').length === 1 ? '' : '文字换场景需保留一个 {{ScenePrompt}} 参数';
  if (!media.some(item => item.type === 'video' && cited.includes(item.token))) return '提示词必须引用原片槽位';
  if (!media.some(item => item.type === 'image' && cited.includes(item.token))) return '提示词必须至少引用一张参考图';
  return '';
}
