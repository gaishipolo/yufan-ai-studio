import type { ReplacementPack, VideoTemplateOut } from '@/shared/api/schema';
import { PACK_ROLES } from '@/entities/template/model/packRoles';
import type { PromptMediaItem } from './PromptMediaView';
import { fixedPackReferences, insertFixedMedia, withoutFixedReferences } from './fixedPackReferences';

/**
 * 提示词预览只需要这四个字段，所以这里用结构类型而不是 `LibraryAssetOut`：
 * 调用方传进来的是工作台的 `SourceVideo`（多几个字段），照契约写反而要在
 * 调用点做一次无意义的投影。
 */
type Source = { id: string; name: string; url?: string | null; previewUrl?: string | null };

/** 编辑时新点名的辅助槽位，必须按保存端 mediaOrder 的用途找图，不能取换包后的偶然顺序。 */
export function templatePromptEditorMedia(template: VideoTemplateOut, source?: Source, pack?: ReplacementPack, currentPacks?: ReplacementPack[]): PromptMediaItem[] {
  if (fixedPackReferences(template).length) return insertFixedMedia(
    templatePromptEditorMedia(withoutFixedReferences(template), source, pack, currentPacks), template);
  const declared = template.mediaOrder ?? [];
  // 多人槽位由已声明的 front 决定，辅助身份图不能被算成新增人物。
  if (template.defaultKind === 'character' || !declared.length || declared.some(item => !item.role)) {
    return templatePromptMedia(template, source, pack, currentPacks);
  }
  const referenceBindings = Object.fromEntries(declared.map(item => [item.token, item.type === 'video' ? 'source_video' : item.role]));
  return templatePromptMedia({...template, rules: {...template.rules, referenceBindings}}, source, pack, currentPacks);
}

/** 仅供准备前预览；历史任务必须使用自己的 inputSummary，不能调用此函数推断。 */
export function templatePromptMedia(template: VideoTemplateOut, source?: Source, pack?: ReplacementPack, currentPacks?: ReplacementPack[]): PromptMediaItem[] {
  if (fixedPackReferences(template).length) return insertFixedMedia(
    templatePromptMedia(withoutFixedReferences(template), source, pack, currentPacks), template);
  const video: PromptMediaItem = {id: source?.id ?? template.sourceVideoAssetId ?? 'source',
    token: '', name: source?.name ?? template.sourceVideoName ?? '原片',
    type: 'video', url: source?.url, previewUrl: source?.previewUrl};
  const declared = template.mediaOrder?.filter(item => item.type === 'image') ?? [];
  const raw = template.rules?.referenceBindings;
  const bindings = raw && typeof raw === 'object' && !Array.isArray(raw) ? raw as Record<string, string> : undefined;
  const entries = bindings ? Object.entries(bindings).sort(([a], [b]) => Number(a.match(/\d+/)?.[0]) - Number(b.match(/\d+/)?.[0])) : [];
  const sourceToken = entries.find(([, role]) => role === 'source_video')?.[0]
    ?? template.mediaOrder?.find(item => item.type === 'video')?.token ?? '{{Mixed 1}}';
  const videoIndex = Math.max(0, Number(sourceToken.match(/\d+/)?.[0] ?? 1) - 1);
  const roles = bindings ? entries.filter(([, role]) => role !== 'source_video').map(([, role]) => role) : declared.map(item => item.role);
  const count = template.defaultKind === 'character'
    ? bindings ? roles.filter(role => role === 'front').length : declared.filter(item => item.role === 'front' && item.inPrompt).length : 1;
  const multiple = count > 1;
  const selected = multiple ? currentPacks ?? (pack ? [pack] : []) : pack ? [pack] : [];
  const refs = selected.map((selectedPack, slot) => selectedPack.referenceIds.map((id, index) => ({id, index, slot, pack: selectedPack, role: selectedPack.bindings?.[index]?.role ?? ''})));
  type Reference = (typeof refs)[number][number];
  const ordered: (Reference | undefined)[] = [];
  if (multiple) {
    for (let slot = 0; slot < count; slot++) {
      const group = refs[slot] ?? [];
      const index = group.findIndex(ref => ref.role === 'front');
      ordered.push(index >= 0 ? group.splice(index, 1)[0] : undefined);
    }
  } else if (!pack && !bindings) {
    const images: PromptMediaItem[] = declared.map(item => ({id: item.assetId ?? item.token,
      token: item.token, name: item.name || '参考图不可用', type: 'image'}));
    images.splice(videoIndex, 0, video);
    return images.map((item, index) => ({...item, token: `{{Mixed ${index + 1}}}`}));
  } else {
    const group = refs[0] ?? [];
    for (const [slot, role] of roles.entries()) {
      let index = role ? group.findIndex(ref => ref.role === role) : -1;
      if (index < 0 && !bindings) index = group.findIndex(ref => ref.id === declared[slot]?.assetId);
      ordered.push(index >= 0 ? group.splice(index, 1)[0] : undefined);
    }
  }
  ordered.push(...refs.flat());
  const seen = new Set<string>();
  const images: PromptMediaItem[] = [];
  for (const [slot, ref] of ordered.entries()) {
    if (!ref) {
      images.push({id: `unselected:${slot}`, token: '', name: multiple ? `第 ${slot + 1} 位人物未选择` : '所需参考图不可用', type: 'image'});
      continue;
    }
    if (seen.has(ref.id)) continue;
    seen.add(ref.id);
    const current = ref.pack;
    const original = declared.find(item => item.assetId === ref.id);
    const role = PACK_ROLES[current.kind]?.[ref.role] || `参考图 ${ref.index + 1}`;
    const url = current.previewUrls?.[ref.index]
      || (current.version ? `/api/v1/templates/replacement-packs/${current.id}/preview/${ref.id}?version=${current.version}` : undefined);
    const owner = multiple ? `第${ref.slot + 1}位人物（${current.name}） · ` : '';
    images.push({id: ref.id, token: '', type: 'image',
      name: `${owner}${original?.name || `${current.name} · ${role}`}`, url, previewUrl: url,
      usage: `${owner}${role}${current.bindings?.[ref.index]?.description ? `：${current.bindings[ref.index].description}` : ''}`});
  }
  images.splice(videoIndex, 0, video);
  return images.map((item, index) => ({...item, token: `{{Mixed ${index + 1}}}`}));
}
