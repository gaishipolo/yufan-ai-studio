import { useId, useRef, useState, type KeyboardEvent } from 'react';
import { AtSign, ImagePlus, Upload, X } from 'lucide-react';
import { useUpdateVideoTemplatePrompt } from '@/entities/video-template/api/videoTemplate.queries';
import { useUploadAsset } from '@/entities/library/api/library.queries';
import { ArchivedThumbnail } from '@/entities/asset/ui/ArchivedThumbnail';
import { ArchivedMedia } from '@/entities/asset/ui/ArchivedMedia';
import { PACK_ROLES } from '@/entities/template/model/packRoles';
import { useCan } from '@/shared/access/use-access';
import type { LibraryAssetOut, ReplacementPack, VideoTemplateOut } from '@/shared/api/schema';
import { Button } from '@/shared/ui';
import { MediaGallery, type PromptMediaItem } from './PromptMediaView';
import { findPromptMention, promptMediaLabel, validateTemplatePrompt, type PromptMention } from './promptMentions';
import { PromptCanvasInput, type PromptCanvasHandle } from './PromptCanvasInput';
import { PromptReferenceMenu, type PromptReferenceKind, type PromptReferenceOption } from './PromptReferenceMenu';
import { fixedBaseCount, packReferencePayload, removeFixedPackReference, type FixedPackReference } from './fixedPackReferences';
import type { PromptRange } from './promptEditorDom';
import './prompt-canvas.css';

// 后端 MAX_REFERENCE_IMAGES(10 张图片) + 1 个原片槽位。保存端按
// `fixedBaseMediaCount + 固定引用数` 校验，这里必须用同一个上限。
const MAX_MIXED_SLOTS = 11;

interface Props {
  template: VideoTemplateOut; media: PromptMediaItem[]; value: string; uploaded: LibraryAssetOut[];
  packs: ReplacementPack[]; references: FixedPackReference[]; dirty: boolean;
  hasSavedReferences: boolean;
  onReferencesChange: (refs: FixedPackReference[]) => void;
  onChange: (text: string) => void; onUploaded: (asset: LibraryAssetOut) => void; onRemoveUpload: (id: string) => void;
  onSaved: (template: VideoTemplateOut) => void; onCancel: () => void; onBusyChange: (busy: boolean) => void;
}

export function TemplatePromptEditor({template, media, value: draft, uploaded, packs, references, dirty, hasSavedReferences, onReferencesChange, onChange, onUploaded, onRemoveUpload, onSaved, onCancel, onBusyChange}: Props) {
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [mention, setMention] = useState<PromptMention | null>(null);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const [referenceKind, setReferenceKind] = useState<PromptReferenceKind>('all');
  const [anchor, setAnchor] = useState({left: 0, top: 0});
  const [focusSearch, setFocusSearch] = useState(false);
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [preview, setPreview] = useState<PromptMediaItem>();
  const input = useRef<PromptCanvasHandle>(null);
  const uploadInput = useRef<HTMLInputElement>(null);
  const composing = useRef(false);
  const saving = useRef(false);
  const selection = useRef<PromptRange>({start: draft.length, end: draft.length});
  const mutation = useUpdateVideoTemplatePrompt();
  const upload = useUploadAsset();
  const canUpload = useCan('library:create');
  const id = useId();
  const current = media.filter(item => template.mediaOrder?.some(slot => slot.token === item.token)).map(item => {
    const slot = template.mediaOrder?.find(slot => slot.token === item.token);
    const role = item.type === 'video' ? '原片' : PACK_ROLES[template.defaultKind ?? 'product']?.[slot?.role ?? ''] ?? '参考图';
    const alias = slot?.role === 'multiview' ? '三视图' : slot?.role === 'front' ? '正面图' : '';
    const owner = packs.find(pack => pack.referenceIds.includes(item.id));
    return {...item, label: `${owner?.name ?? ''} · ${item.name} · ${role}${alias ? `（${alias}）` : ''}`, disabled: item.type === 'image' && !item.url};
  });
  const currentIds = new Set(current.map(item => item.id));
  const referencedIds = new Set(references.map(ref => ref.assetId));
  const seenPackaged = new Set<string>();
  const packaged = packs.flatMap(pack => pack.referenceIds.filter(assetId => !currentIds.has(assetId)).map(assetId => {
    const index = pack.referenceIds.indexOf(assetId);
    const role = PACK_ROLES[pack.kind]?.[pack.bindings?.[index]?.role ?? ''] ?? '参考图';
    const name = pack.referenceNames?.[index] || `${pack.name} · ${role}`;
    const url = pack.previewUrls?.[index] || `/api/v1/templates/replacement-packs/${pack.id}/preview/${assetId}?version=${pack.version}`;
    const catalogRef: FixedPackReference = {token: '', packId: pack.id, packVersion: pack.version ?? 0,
      assetId, name, packName: pack.name, url, type: 'image'};
    return {id: assetId, token: `pack:${pack.id}:${pack.version}:${assetId}`, name, type: 'image', url, previewUrl: url,
      label: `${pack.name} · ${name} · ${role}`, subtitle: `${pack.kind === 'character' ? '人物' : pack.kind === 'scene' ? '场景' : '产品'}素材包 · ${role}`,
      disabled: pack.available === false || !pack.version, catalogRef};
  }))
    // 同一张图可能被多个包收录；后端按 assetId 拒绝重复引用，这里先去重、
    // 再把已引用项禁用，避免「选了两次 → 保存必 400」且界面看不出原因。
    .filter(item => {
      if (seenPackaged.has(item.id)) return false;
      seenPackaged.add(item.id);
      return true;
    })
    .map(item => ({...item, disabled: item.disabled || referencedIds.has(item.id)}));
  const options: (PromptReferenceOption & {catalogRef?: FixedPackReference})[] = [...current, ...packaged]
    .filter(item => (referenceKind === 'all' || item.type === referenceKind) && (!query || `${item.label} ${item.token}`.toLocaleLowerCase().includes(query.toLocaleLowerCase())))
    .map(item => ({...item, referenced: 'catalogRef' in item ? referencedIds.has(item.id) : !!item.token && draft.includes(item.token)}))
    .sort((a, b) => Number(!!b.referenced) - Number(!!a.referenced));
  const selectable = options.filter(item => !item.disabled);
  const activeItem = selectable[active % Math.max(1, selectable.length)];

  function openMenu(range: PromptMention, search = false) {
    const rect = input.current?.caretRect();
    const width = Math.min(304, window.innerWidth - 24);
    setAnchor({left: Math.max(12, Math.min(rect?.left ?? 24, window.innerWidth - width - 12)), top: Math.max(12, Math.min((rect?.bottom ?? 120) + 8, window.innerHeight - 400))});
    setMention(range); setQuery(range.query); setActive(0); setReferenceKind('all'); setFocusSearch(search);
  }
  function sync(text: string, range: PromptRange) {
    selection.current = range;
    if (composing.current) return;
    const next = findPromptMention(text, range.start, range.end);
    if (next) openMenu(next); else setMention(null);
  }
  function choose(token: string) {
    if (!mention || saving.current) return;
    const selected = options.find(item => item.token === token);
    let actualToken = token;
    const catalogRef = selected?.catalogRef;
    if (catalogRef) {
      const existing = references.find(ref => ref.assetId === catalogRef.assetId);
      if (existing) {
        // 同一张图已在引用中：复用它的编号，只插入正文标记，不新增引用。
        // 后端 resolve_pack_references 会按 assetId 拒绝重复，新增引用必然保存失败。
        actualToken = existing.token;
      } else {
        // media.length 含当前包的辅助图，会虚高；与后端同用「基础 + 固定引用」口径。
        if (fixedBaseCount(template) + references.length + 1 > MAX_MIXED_SLOTS) {
          setError(`每次最多使用${MAX_MIXED_SLOTS - 1}张参考图，请先减少参考图`);
          return;
        }
        actualToken = `{{Mixed ${fixedBaseCount(template) + references.length + 1}}}`;
        onReferencesChange([...references, {...catalogRef, token: actualToken}]);
      }
    }
    input.current?.replace(mention, actualToken);
    setMention(null); setError('');
  }
  function keyDown(event: KeyboardEvent<HTMLElement>) {
    if (composing.current || event.nativeEvent.isComposing || event.keyCode === 229) {event.stopPropagation(); return;}
    if (!mention) return;
    if (event.key === 'Escape') {event.preventDefault(); event.stopPropagation(); setMention(null);}
    else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault(); setActive(index => (index + (event.key === 'ArrowDown' ? 1 : -1) + selectable.length) % Math.max(1, selectable.length));
    } else if (event.key === 'Enter' && activeItem?.token) {event.preventDefault(); choose(activeItem.token);}
  }
  async function uploadFiles(files: File[]) {
    if (saving.current || !canUpload || !files.length) return;
    if (files.some(file => !/\.(png|jpe?g|webp)$/i.test(file.name))) {setError('请上传 PNG、JPG 或 WebP 图片'); return;}
    if (uploaded.length + files.length > 10) {setError('当前功能最多暂存 10 张固定参考图'); return;}
    saving.current = true; setBusy(true); setUploading(true); onBusyChange(true); setError(''); setMention(null);
    try {
      for (const file of files) onUploaded(await upload.mutateAsync({file, kind: 'image'}));
      setSuccess('图片已上传，固定参考图的生成调用后续接入。');
    } catch (reason) {setError(reason instanceof Error ? reason.message : '上传失败，请重试');}
    finally {saving.current = false; setBusy(false); setUploading(false); onBusyChange(false); if (uploadInput.current) uploadInput.current.value = '';}
  }
  async function save() {
    if (saving.current) return;
    const problem = validateTemplatePrompt(draft, template);
    if (problem) {setError(problem); return;}
    saving.current = true; setBusy(true); onBusyChange(true); setError('');
    try {onSaved(await mutation.mutateAsync({id: template.id, prompt: draft,
      ...(references.length || hasSavedReferences ? {references: packReferencePayload(references)} : {})})); setSuccess('当前功能提示词已保存');}
    catch (reason) {setError(reason instanceof Error ? reason.message : '保存失败，请重试');}
    finally {saving.current = false; setBusy(false); onBusyChange(false);}
  }

  return <section aria-label="编辑封装提示词" className="space-y-4">
    <div className="flex flex-wrap items-center justify-between gap-2"><label htmlFor={`${id}-prompt`} className="text-sm font-semibold text-ink">封装提示词原文</label><span className="text-xs text-ink-mute">引用标签仅改变显示，原始标记和正文保持不变</span></div>
    <div className="prompt-canvas-shell">
      <div className="prompt-canvas-toolbar">
        {canUpload && <Button size="sm" variant="ghost" icon={<Upload size={14} />} loading={uploading} disabled={busy} onClick={() => uploadInput.current?.click()}>上传图片</Button>}
        <Button size="sm" variant="ghost" icon={<AtSign size={14} />} disabled={busy} onClick={() => openMenu({...selection.current, query: ''}, true)}>插入引用 @</Button>
        <span className="ml-auto text-xs text-ink-mute">{media.length} 个输入</span>
      </div>
      <div className="prompt-canvas-media" aria-label="模板参考素材">{media.map(item => <button key={item.token} type="button" title={`${promptMediaLabel(item)} · ${item.name}`} aria-label={`预览素材：${item.name}`} onClick={() => setPreview(item)}><ArchivedThumbnail url={item.previewUrl || (item.type === 'image' ? item.url : undefined)} type={item.type} /><span>{item.token?.match(/\d+/)?.[0]}</span></button>)}</div>
      {references.length > 0 && <div className="flex flex-wrap gap-2 pb-2" aria-label="已绑定封装参考图">{references.map(ref => <span key={ref.token} className="inline-flex items-center gap-1 rounded-md border border-line px-2 py-1 text-xs text-ink-soft"><span className="max-w-40 truncate">{ref.name}</span><button type="button" disabled={busy} aria-label={`移除固定引用：${ref.name}`} title="移除图片及其正文引用标记，其余固定引用同步重编号" onClick={() => {
        const next = removeFixedPackReference(draft, references, ref.assetId, fixedBaseCount(template));
        onReferencesChange(next.references); onChange(next.prompt); setMention(null); setError('');
      }}><X size={12} /></button></span>)}</div>}
      <PromptCanvasInput ref={input} id={`${id}-prompt`} value={draft} media={media} disabled={busy} describedBy={`${id}-hint`}
        activeOption={mention && activeItem ? `${id}-options-option-${options.indexOf(activeItem)}` : undefined}
        onChange={(text, range) => {onChange(text); setError(''); setSuccess(''); sync(text, range);}}
        onSelection={range => {selection.current = range; if (mention && (range.start !== mention.end || range.end !== mention.end)) setMention(null);}} onKeyDown={keyDown}
        onComposing={value => {composing.current = value; if (value) setMention(null);}}
        onPreview={setPreview} onFiles={files => {void uploadFiles(files);}} />
      <div className="prompt-canvas-bottom"><span id={`${id}-hint`}>@ 引用素材 · ↑↓ 选择 · Enter 插入 · Esc 收起</span><span>{draft.length}/20000</span></div>
    </div>
    {mention && <PromptReferenceMenu id={`${id}-options`} options={options} prompt={draft} query={query} active={active} anchor={anchor} focusSearch={focusSearch} kind={referenceKind} onKind={setReferenceKind} onQuery={setQuery} onActive={setActive} onChoose={choose} onClose={() => setMention(null)} onKeyDown={keyDown} />}
    <input ref={uploadInput} type="file" multiple hidden accept=".png,.jpg,.jpeg,.webp" aria-label="上传模板固定参考图" onChange={event => {void uploadFiles([...(event.target.files ?? [])]);}} />
    {uploaded.length > 0 && <div className="space-y-2 rounded-xl border border-dashed border-line p-3" aria-label="新上传固定参考图">
      <p className="flex items-center gap-2 text-xs font-semibold text-ink"><ImagePlus size={14} />当前功能固定参考图 · 待接入生成</p>
      <p className="text-xs text-ink-mute">文件已存入素材库；本次编辑会话内保留。模板绑定与生成调用将在后续迭代接入。</p>
      <div className="flex flex-wrap gap-2">{uploaded.map(asset => <div key={asset.id} className="flex items-center gap-2 rounded-lg border border-line p-2 text-xs">
        <button type="button" aria-label={`预览上传图片：${asset.name}`} onClick={() => setPreview({id: asset.id, name: asset.name, url: asset.url, previewUrl: asset.previewUrl, type: 'image'})}><span className="block h-10 w-12"><ArchivedThumbnail url={asset.previewUrl || asset.url} /></span></button>
        <span className="max-w-40 truncate">{asset.name}</span><button type="button" disabled={busy} aria-label={`移出当前固定参考区：${asset.name}`} title="仅移出当前参考区，素材库中的文件仍保留" onClick={() => onRemoveUpload(asset.id)}><X size={13} /></button>
      </div>)}</div>
    </div>}
    {preview && <section aria-label="引用素材预览" className="rounded-xl border border-line bg-panel-soft p-3"><div className="mb-2 flex items-center justify-between"><p className="text-xs font-semibold">{preview.name}</p><Button size="sm" onClick={() => setPreview(undefined)}>收起预览</Button></div>{preview.url ? <ArchivedMedia url={preview.url} type={preview.type} name={preview.name} downloadAllowed={false} /> : <p className="text-xs text-ink-mute">当前素材暂不可预览</p>}</section>}
    {error && <p role="alert" className="text-sm text-danger">{error}</p>}
    {success && <p role="status" className="text-xs text-primary">{success}</p>}
    <div className="flex flex-wrap items-center justify-between gap-2"><span className="text-xs text-ink-mute">保存当前提示词与引用绑定，不附加图片描述。</span><div className="flex gap-2"><Button disabled={busy} onClick={onCancel}>结束编辑</Button><Button variant="primary" loading={busy && !uploading} disabled={busy || !dirty} onClick={() => {void save();}}>保存提示词</Button></div></div>
    <details><summary className="cursor-pointer text-xs text-primary">查看实际保存原文</summary><pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-line p-3 text-xs leading-6">{draft}</pre></details>
    <details><summary className="cursor-pointer text-xs text-primary">核对当前完整输入</summary><div className="mt-3"><MediaGallery media={media} prompt={draft} preview /></div></details>
  </section>;
}
