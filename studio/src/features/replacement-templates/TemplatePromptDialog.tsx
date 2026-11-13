import { useState } from 'react';
import type { LibraryAssetOut, ReplacementPack, VideoTemplateOut } from '@/shared/api/schema';
import type { SourceVideo } from './sourceVideo';
import { Button, Modal } from '@/shared/ui';
import { Can } from '@/shared/access/Can';
import { MediaGallery, PromptMediaView } from './PromptMediaView';
import { templatePromptEditorMedia, templatePromptMedia } from './templatePromptMedia';
import { videoTemplateLabel } from './videoTemplateFunctions';
import { TemplatePromptEditor } from './TemplatePromptEditor';
import { fixedPackReferences, packReferencePayload, withFixedReferences, type FixedPackReference } from './fixedPackReferences';

interface Props {
  source?: SourceVideo;
  scenePrompt?: string;
  templates: VideoTemplateOut[];
  packs: ReplacementPack[];
  selectedTemplateId?: string;
  currentPack?: ReplacementPack;
  currentPacks?: ReplacementPack[];
  onClose: () => void;
}

export function TemplatePromptDialog({source, templates, packs, selectedTemplateId, scenePrompt, currentPack, currentPacks, onClose}: Props) {
  const [selected, setSelected] = useState(selectedTemplateId ?? templates[0]?.id ?? '');
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const [exitTarget, setExitTarget] = useState<'close' | 'preview'>('close');
  const [saved, setSaved] = useState<Record<string, VideoTemplateOut>>({});
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [uploads, setUploads] = useState<Record<string, LibraryAssetOut[]>>({});
  const [referenceDrafts, setReferenceDrafts] = useState<Record<string, FixedPackReference[]>>({});
  const base = templates.find(item => item.id === selected) ?? templates[0];
  const latest = base && saved[base.id];
  const template = latest && latest.updatedAt >= base.updatedAt ? latest : base;
  if (!template) return null;
  const useCurrent = template.id === (selectedTemplateId ?? templates[0]?.id);
  const selectedCurrentPack = useCurrent ? currentPack : undefined;
  const selectedCurrentPacks = useCurrent ? currentPacks : undefined;
  const pack = selectedCurrentPack ?? packs.find(item => item.id === template.packId);
  const textScene = template.rules?.inputMode === 'video_scene_text';
  const description = (useCurrent ? scenePrompt : undefined) ?? (typeof template.rules?.scenePromptDefault === 'string' ? template.rules.scenePromptDefault : '');
  const prompt = textScene ? template.prompt.replaceAll('{{ScenePrompt}}', () => description) : template.prompt;
  const references = referenceDrafts[template.id] ?? fixedPackReferences(template);
  const editorTemplate = withFixedReferences(template, references);
  const media = templatePromptMedia(template, source, textScene ? undefined : pack, textScene ? undefined : selectedCurrentPacks);
  const editorMedia = templatePromptEditorMedia(editorTemplate, source, textScene ? undefined : pack, textScene ? undefined : selectedCurrentPacks);
  const missingReferences = media.some(item => item.type === 'image' && !item.url);
  const selectionKey = selectedCurrentPacks?.map(item => `${item.id}:${item.version}`).join(',') ?? pack?.id;
  const dirty = templates.filter(item => (drafts[item.id] !== undefined && drafts[item.id] !== (saved[item.id] ?? item).prompt)
    || (referenceDrafts[item.id] !== undefined && JSON.stringify(packReferencePayload(referenceDrafts[item.id])) !== JSON.stringify(packReferencePayload(fixedPackReferences(saved[item.id] ?? item)))));
  function finish(target: 'close' | 'preview') {
    if (busy) return;
    if (editing && dirty.length) {setExitTarget(target); setConfirmDiscard(true); return;}
    if (target === 'close') onClose(); else setEditing(false);
  }
  return <Modal open onClose={() => finish('close')} title="模板提示词与输入预览" width={1000}>
    <div className="space-y-4 p-5">
      <p className="text-sm font-semibold text-ink">{template.name}</p>
      {templates.length > 1 && <label className="block space-y-1 text-xs text-ink-soft">查看封装功能
        <select aria-label="查看封装功能" disabled={busy} value={template.id} onChange={event => {setSelected(event.target.value); setConfirmDiscard(false);}} className="ml-2 rounded-lg border border-line bg-panel p-2">
          {templates.map(item => <option key={item.id} value={item.id}>{videoTemplateLabel(item)} · {item.name}{dirty.some(draft => draft.id === item.id) ? ' · 未保存' : ''}</option>)}
        </select>
      </label>}
      {confirmDiscard && <div className="space-y-2 rounded-lg border border-line p-3" role="alert">
        <p className="text-sm text-ink">将放弃 {dirty.length} 个功能尚未保存的草稿。已上传的图片仍保留在素材库。</p>
        <div className="flex gap-2"><Button onClick={() => setConfirmDiscard(false)}>继续编辑</Button><Button variant="danger" disabled={busy} onClick={() => {setDrafts({}); setReferenceDrafts({}); setConfirmDiscard(false); if (exitTarget === 'close') onClose(); else setEditing(false);}}>{exitTarget === 'close' ? '放弃并关闭' : '放弃草稿并结束'}</Button></div>
      </div>}
      {editing && templates.length > 1 && <p className="text-xs text-ink-mute">切换功能会保留各自草稿，保存仅更新当前功能。{dirty.length > 0 ? `当前有 ${dirty.length} 个功能尚未保存。` : ''}</p>}
      {editing ? <TemplatePromptEditor key={template.id} template={editorTemplate} media={editorMedia} onBusyChange={setBusy}
        packs={packs} references={references} hasSavedReferences={fixedPackReferences(template).length > 0} onReferencesChange={refs => setReferenceDrafts(previous => ({...previous, [template.id]: refs}))} dirty={dirty.some(item => item.id === template.id)}
        value={drafts[template.id] ?? template.prompt} onChange={text => setDrafts(previous => ({...previous, [template.id]: text}))}
        uploaded={uploads[template.id] ?? []} onUploaded={asset => setUploads(previous => ({...previous, [template.id]: [...(previous[template.id] ?? []), asset]}))}
        onRemoveUpload={id => setUploads(previous => ({...previous, [template.id]: (previous[template.id] ?? []).filter(asset => asset.id !== id)}))}
        onSaved={updated => {setSaved(previous => ({...previous, [updated.id]: updated})); setDrafts(previous => {const next = {...previous}; delete next[updated.id]; return next;}); setReferenceDrafts(previous => {const next = {...previous}; delete next[updated.id]; return next;}); setConfirmDiscard(false);}}
        onCancel={() => finish('preview')} /> : <>
      <p className="rounded-lg bg-panel-soft p-3 text-xs leading-5 text-ink-soft">准备前预览 · {textScene ? '文字换场景：只上传原片，使用当前场景描述' : <>{selectedCurrentPacks?.length ? '当前所选人物：' : selectedCurrentPack ? '当前所选素材包：' : '默认素材包：'}{selectedCurrentPacks?.length ? selectedCurrentPacks.map(item => item.name).join('、') : pack?.name ?? template.packName ?? '暂不可用'}</>}。创建任务后以当次输入快照为准；这里只查看，不提交生成。</p>
      {(!textScene && (!pack || pack.available === false || missingReferences)) && <p role="status" className="text-xs text-danger">请补齐人物选择或所需参考用途；媒体预览可能不完整，准备任务时仍需校验。</p>}
      <MediaGallery key={`gallery:${template.id}:${selectionKey}`} media={media} prompt={prompt} preview />
      <div className="flex items-center justify-between gap-2"><p className="text-xs font-semibold text-ink">封装提示词 · 点击缩略图核对引用</p>
        <Can permission={['template:read', 'template:pack:manage']}><Button size="sm" onClick={() => setEditing(true)}>编辑提示词</Button></Can>
      </div>
      {template.prompt.includes('{{ReplacementScope}}') && <p className="text-xs text-ink-mute">范围参数将在准备任务时替换为你的选择。</p>}
      <PromptMediaView key={`prompt:${template.id}:${selectionKey}`} prompt={prompt} media={media} />
      </>}
    </div>
  </Modal>;
}
