import type { VideoTemplateMedia, VideoTemplateOut, VideoTemplatePackReferenceIn } from '@/shared/api/schema';
import type { PromptMediaItem } from './PromptMediaView';

export interface FixedPackReference extends VideoTemplatePackReferenceIn { name: string; packName?: string; url: string; type: 'image' }

export function fixedPackReferences(template: VideoTemplateOut): FixedPackReference[] {
  const raw = template.rules?.fixedReferences;
  return Array.isArray(raw) ? raw.filter((item): item is FixedPackReference => item && typeof item.token === 'string'
    && typeof item.packId === 'string' && typeof item.assetId === 'string' && typeof item.packVersion === 'number'
    && typeof item.name === 'string' && typeof item.url === 'string') : [];
}
export function packReferencePayload(references: FixedPackReference[]): VideoTemplatePackReferenceIn[] {
  return references.map(({token, packId, packVersion, assetId}) => ({token, packId, packVersion, assetId}));
}
export function fixedBaseCount(template: VideoTemplateOut) {
  const count = template.rules?.fixedBaseMediaCount;
  return typeof count === 'number' ? count : template.mediaOrder?.length ?? 0;
}
export function removeFixedPackReference(prompt: string, refs: FixedPackReference[], assetId: string, base: number) {
  const remaining = refs.filter(ref => ref.assetId !== assetId);
  const tokens = new Map(refs.map(ref => [ref.token, ref.assetId === assetId ? ''
    : `{{Mixed ${base + remaining.findIndex(item => item.assetId === ref.assetId) + 1}}}`]));
  return {prompt: prompt.replace(/\{\{Mixed \d+\}\}/g, token => tokens.get(token) ?? token),
    references: remaining.map((ref, index) => ({...ref, token: `{{Mixed ${base + index + 1}}}`}))};
}
export function withoutFixedReferences(template: VideoTemplateOut): VideoTemplateOut {
  const rules = {...template.rules};
  delete rules.fixedReferences; delete rules.fixedBaseMediaCount;
  return {...template, rules, mediaOrder: template.mediaOrder?.slice(0, fixedBaseCount(template))};
}
export function withFixedReferences(template: VideoTemplateOut, refs: FixedPackReference[]): VideoTemplateOut {
  const base = withoutFixedReferences(template);
  const count = base.mediaOrder?.length ?? 0;
  const extra: VideoTemplateMedia[] = refs.map(ref => ({token: ref.token, assetId: ref.assetId, name: ref.name,
    role: 'fixed', type: 'image', inPrompt: template.prompt.includes(ref.token)}));
  return {...base, mediaOrder: [...base.mediaOrder ?? [], ...extra], rules: {...base.rules,
    ...(refs.length ? {fixedReferences: refs, fixedBaseMediaCount: count} : {})}};
}
export function insertFixedMedia(media: PromptMediaItem[], template: VideoTemplateOut): PromptMediaItem[] {
  const count = fixedBaseCount(template);
  const base = [...media];
  while (base.length < count) base.push({id: `missing:${base.length}`, name: '基础参考图不可用', type: 'image'});
  const extra: PromptMediaItem[] = fixedPackReferences(template).map(ref => ({id: ref.assetId, token: ref.token,
    name: ref.name, type: 'image', url: ref.url, previewUrl: ref.url}));
  const fixedIds = new Set(extra.map(item => item.id));
  return [...base.slice(0, count), ...extra, ...base.slice(count).filter(item => !fixedIds.has(item.id))]
    .map((item, index) => ({...item, token: `{{Mixed ${index + 1}}}`}));
}
