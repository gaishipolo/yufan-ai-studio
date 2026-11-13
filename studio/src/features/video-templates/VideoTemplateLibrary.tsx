import { useState } from 'react';
import { useArchiveAssets } from '@/entities/asset/api/archive.queries';
import { useAvailablePacks } from '@/entities/template/api/pack.queries';
import { baseRole, rolesFor } from '@/entities/template/model/packRoles';
import { useVideoTemplateMutations, useVideoTemplates } from '@/entities/video-template/api/videoTemplate.queries';
import type { VideoTemplateDraftIn, VideoTemplateOut } from '@/shared/api/schema';
import { Button, EmptyState, ErrorState, Modal } from '@/shared/ui';
import { toast } from '@/shared/lib/toast';

interface FormState {
  name: string;
  templateKey: string;
  description: string;
  prompt: string;
  sourceVideoName: string;
  sourceVideoAssetId: string;
  sourceVideoPath: string;
  sourceDurationSec: string;
  aspectRatio: string;
  packId: string;
  defaultKind: 'character' | 'product' | 'scene';
  pinned: boolean;
  /** 产品图的媒体顺序：下标 +2 就是 {{Mixed N}}，决定提示词里的编号指向哪张图 */
  order: string[];
  generation?: Record<string, unknown>;
  rules?: Record<string, unknown>;
  workbenchLabel?: string;
  scopeOptionsText?: string;
  textScene?: boolean;
  scenePromptDefault?: string;
}

interface PackOption {
  id: string;
  kind?: FormState['defaultKind'];
  label: string;
  images: number;
  assetIds: string[];
  previews: string[];
}

interface SourceOption {
  id: string;
  label: string;
  name: string;
  durationSec?: number;
}

const ASPECTS = ['9:16', '16:9', '1:1'];
const KINDS = [
  {value: 'character' as const, label: '人物替换'},
  {value: 'product' as const, label: '产品替换'},
  {value: 'scene' as const, label: '场景替换'},
];

function emptyForm(): FormState {
  return {
    name: '', templateKey: '', description: '', prompt: '',
    sourceVideoName: '', sourceVideoAssetId: '', sourceVideoPath: '', sourceDurationSec: '', aspectRatio: '9:16',
    packId: '', defaultKind: 'product', pinned: false, order: [],
  };
}

/** 「复制固定蓝本」：字段全部沿用，只换名称与标识；媒体顺序照抄（提示词里的编号才对得上）。 */
function formFromBlueprint(template: VideoTemplateOut): FormState {
  return {
    name: `${template.name} · 副本`,
    templateKey: '',
    description: template.description ?? '',
    prompt: template.prompt,
    sourceVideoName: template.sourceVideoName ?? '',
    sourceVideoAssetId: template.sourceVideoAssetId ?? '',
    sourceVideoPath: template.sourceVideoPath ?? '',
    sourceDurationSec: String(template.sourceDurationSec ?? 0),
    aspectRatio: template.aspectRatio || '9:16',
    packId: template.packId ?? '',
    defaultKind: template.defaultKind ?? 'product',
    pinned: false,
    order: (template.mediaOrder ?? [])
      .filter(item => item.type === 'image' && item.assetId)
      .map(item => item.assetId as string),
    generation: template.generation,
    rules: template.rules,
    textScene: template.rules?.inputMode === 'video_scene_text',
    scenePromptDefault: typeof template.rules?.scenePromptDefault === 'string' ? template.rules.scenePromptDefault : '',
    workbenchLabel: typeof template.rules?.workbenchLabel === 'string' ? template.rules.workbenchLabel : '',
    scopeOptionsText: Array.isArray(template.rules?.scopeOptions) ? template.rules.scopeOptions.join('\n') : '',
  };
}

function move<T>(list: T[], from: number, to: number): T[] {
  if (to < 0 || to >= list.length || from === to) return list;
  const next = [...list];
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next;
}

/** 模板是否声明了用途槽位。`referenceBindings` 是唯一判据，空对象也算声明。 */
function hasBindings(form: FormState): boolean {
  const raw = form.rules?.referenceBindings;
  return !!raw && typeof raw === 'object' && !Array.isArray(raw);
}

/** 除原片之外的槽位用途，顺序即 `{{Mixed 2}}` 起的编号。 */
function slotRolesOf(form: FormState): string[] {
  const raw = form.rules?.referenceBindings;
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return [];
  return Object.values(raw as Record<string, string>).filter(role => role !== 'source_video');
}

/** 重写槽位：原片恒在第 1 位，其余按传进来的用途顺序重排编号（删中间一项不会留空洞）。 */
function withSlots(form: FormState, roles: string[]): Record<string, unknown> {
  return {
    ...(form.rules ?? {}),
    referenceBindings: {
      '{{Mixed 1}}': 'source_video',
      ...Object.fromEntries(roles.map((role, index) => [`{{Mixed ${index + 2}}}`, role])),
    },
  };
}

function withoutSlots(form: FormState): Record<string, unknown> {
  const rules = {...(form.rules ?? {})};
  delete rules.referenceBindings;
  return rules;
}

/** 前端先挡一轮：token 编号必须落在「原片 + 素材包参考图」范围内。 */
function validateForm(form: FormState, images: number): string {
  const scopes = (form.scopeOptionsText ?? '').split('\n').map(item => item.trim()).filter(Boolean);
  if (scopes.length > 30 || scopes.join('、').length > 1000 || new Set(scopes).size !== scopes.length
    || scopes.some(item => item.length > 100 || /、|\{\{|\}\}/.test(item))) return '范围选项需唯一，每项最多100字，合计最多1000字，不含顿号或引用标记';
  if (!!scopes.length !== form.prompt.includes('{{ReplacementScope}}')) return '配置范围选项时，请在提示词中加入 {{ReplacementScope}}；无选项时请移除该参数';
  if (!form.name.trim()) return '请填写模板名称';
  if (!form.sourceVideoAssetId) return '请选择样片原片（先在「我的原片」上传登记，绑定后才会出现在全部模板里）';
  if (form.textScene) {
    if (form.defaultKind !== 'scene') return '文字换场景必须使用场景替换类型';
    const description = form.scenePromptDefault ?? '';
    if (!description.trim() || description.length > 4000 || /\{\{|\}\}/.test(description)) return '请填写默认场景描述，最多4000字，不含模板标记';
    const tokens = [...form.prompt.matchAll(/\{\{([^{}]+)\}\}/g)].map(match => match[1]);
    if (tokens.filter(token => token === 'ScenePrompt').length !== 1 || tokens.some(token => !['ScenePrompt', 'Mixed 1'].includes(token)) || scopes.length) return '文字换场景需包含一个 {{ScenePrompt}}，只允许引用原视频 {{Mixed 1}}，不配置范围';
    return '';
  }
  /* 用途绑定模式下媒体清单由服务端按声明生成、`mediaOrder` 不随请求发送，
     于是 `order` 不再是这份清单的来源，不能再拿它和素材包图片数做等值校验。
     蓝本里的「运行时选卡」空槽会被 `formFromBlueprint` 滤掉，包里有辅助图时
     两者必然不等——以前这里会把「复制固定蓝本」堵在一个用户看不见原因的报错上。 */
  const declaredBindings = form.rules?.referenceBindings;
  const usesBindings = hasBindings(form);
  if (!form.packId) {
    /* **只声明槽位**：不绑默认包是合法配置，但前提是模板已经声明了用途绑定，
       否则服务端拿不到「要几张图、各是什么用途」。报错要说清下一步点哪里，
       而不是只说「不能为空」。 */
    if (!usesBindings) return '请选择已发布的替换素材包，或先复制一份带用途绑定的固定蓝本再清空素材包';
  } else if (images < 1) {
    return '所选素材包没有参考图，无法生成媒体顺序';
  }
  if (!usesBindings && form.order.length !== images) return '媒体顺序与素材包图片数量对不上，请重新选择素材包';
  if (!form.prompt.trim()) return '请填写固定提示词';
  const tokens = [...form.prompt.matchAll(/\{\{([^{}]+)\}\}/g)].map(match => match[1]);
  if (!tokens.length) return '提示词缺少 {{Mixed N}} 占位符';
  const numbers: number[] = [];
  for (const token of tokens) {
    if (token === 'ReplacementScope') continue;
    const matched = /^Mixed (\d+)$/.exec(token);
    if (!matched) return `非法占位符 {{${token}}}，只允许 {{Mixed N}}`;
    numbers.push(Number(matched[1]));
  }
  const declared = declaredBindings;
  const bindings = declared && typeof declared === 'object' && !Array.isArray(declared)
    ? declared as Record<string, unknown> : undefined;
  const sourceToken = bindings && Object.entries(bindings).find(([, role]) => role === 'source_video')?.[0];
  const sourceSlot = Number(sourceToken?.match(/\d+/)?.[0] ?? 1);
  if (!numbers.includes(sourceSlot)) return `提示词必须引用 {{Mixed ${sourceSlot}}} 原片`;
  if (!numbers.some(number => number !== sourceSlot)) return '提示词必须至少点名一张产品图';
  const max = Math.max(1 + images, Object.keys(bindings ?? {}).length);
  if (numbers.some(number => number > max)) {
    return form.packId
      ? `编号超出范围：该素材包 ${images} 张图，最大可用 {{Mixed ${max}}}`
      : `编号超出范围：模板声明了 ${Object.keys(bindings ?? {}).length} 个用途槽位，最大可用 {{Mixed ${max}}}`;
  }
  return '';
}

function VideoTemplateEditor({initial, packs, sources, busy, onClose, onSubmit}: {
  initial: FormState;
  packs: PackOption[];
  sources: SourceOption[];
  busy: boolean;
  onClose: () => void;
  onSubmit: (payload: VideoTemplateDraftIn) => Promise<void>;
}) {
  const [form, setForm] = useState<FormState>(initial);
  const [error, setError] = useState('');
  const patch = (next: Partial<FormState>) => setForm(value => ({...value, ...next}));
  const images = packs.find(pack => pack.id === form.packId)?.images ?? 0;
  const slotRoles = slotRolesOf(form);
  const slotLabels = rolesFor(form.defaultKind);
  const usesBindings = hasBindings(form);
  const input = 'w-full rounded-lg border border-line bg-panel p-2 text-xs';

  async function submit() {
    const problem = validateForm(form, images);
    if (problem) { setError(problem); return; }
    setError('');
    const tokens = [...form.prompt.matchAll(/\{\{([^{}]+)\}\}/g)].map(match => `{{${match[1]}}}`);
    const payload: VideoTemplateDraftIn = {
      name: form.name.trim(),
      templateKey: form.templateKey.trim(),
      description: form.description,
      prompt: form.prompt,
      sourceVideoName: form.sourceVideoName,
      sourceVideoAssetId: form.sourceVideoAssetId || null,
      sourceVideoPath: form.sourceVideoPath,
      sourceDurationSec: Number(form.sourceDurationSec) || 0,
      aspectRatio: form.aspectRatio,
      packId: form.textScene || !form.packId ? null : form.packId,
      defaultKind: form.defaultKind,
      // 顺序由这里定：{{Mixed 1}} 永远是原片，其后按 form.order 排，提示词里的编号就指向它
      mediaOrder: form.textScene || form.rules?.referenceBindings ? [] : [
        {
          token: '{{Mixed 1}}', role: 'source_video', type: 'video',
          assetId: form.sourceVideoAssetId || null, name: form.sourceVideoName,
          file: form.sourceVideoPath, inPrompt: tokens.includes('{{Mixed 1}}'),
        },
        ...form.order.map((assetId, index) => {
          const token = `{{Mixed ${index + 2}}}`;
          return {token, role: '', type: 'image', assetId,
            name: '', file: '', inPrompt: tokens.includes(token)};
        }),
      ],
      generation: form.generation,
      rules: {...form.rules, ...(form.textScene ? {inputMode: 'video_scene_text', scenePromptDefault: form.scenePromptDefault} : {}), workbenchLabel: (form.workbenchLabel ?? '').trim(),
        scopeOptions: (form.scopeOptionsText ?? '').split('\n').map(item => item.trim()).filter(Boolean)},
      pinned: form.pinned,
    };
    try {
      await onSubmit(payload);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '封装失败，请稍后重试');
    }
  }

  return <Modal open onClose={onClose} title="封装视频模板" width={860}>
    <form className="space-y-4 p-4" onSubmit={event => {event.preventDefault(); void submit();}}>
      <p className="text-xs leading-relaxed text-ink-mute">
        {form.textScene ? '文字换场景只输入原片。固定提示词用 {{ScenePrompt}} 标记用户可编辑的场景描述段落，其余约束保留原文。' : <>媒体顺序由下面的清单决定：{'{{Mixed 1}}'} 固定是原片，后面按你排的顺序排，提示词写 {`{{Mixed 2}}`} 就指向第 2 位那张图 —— 想让三视图当前者，把它移到第 2 位，提示词原文不用改。
        绑定样片原片后，这条模板会和 0507 高端姐一样出现在「全部模板」卡片流里；选中卡片即自动套用默认替换配置。
        提示词按用途引用即可：换别的产品包时后端会按「正面 / 三视图 / 细节」重新取图，同一份提示词不用改。</>}
      </p>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block text-xs">模板名称<input className={input} maxLength={160} required value={form.name} onChange={event => patch({name: event.target.value})} placeholder="例如：米兰之夜 · 3030 粉色" /></label>
        <label className="block text-xs">稳定标识（可选）<input className={input} maxLength={80} value={form.templateKey} onChange={event => patch({templateKey: event.target.value})} placeholder="留空自动生成" /></label>
        <label className="flex items-center gap-2 text-xs sm:col-span-2"><input type="checkbox" checked={!!form.textScene} onChange={event => patch({textScene: event.target.checked, packId: '', order: [], scopeOptionsText: '', rules: {}, ...(event.target.checked ? {defaultKind: 'scene'} : {})})} />文字换场景（仅原片和场景描述）</label>
        {form.textScene ? <label className="block text-xs sm:col-span-2">默认场景描述<textarea className={input} rows={4} maxLength={4000} value={form.scenePromptDefault ?? ''} onChange={event => patch({scenePromptDefault: event.target.value})} /></label> : <>
        <label className="block text-xs sm:col-span-2">替换素材包
          <select className={input} value={form.packId} onChange={event => {
            const picked = packs.find(pack => pack.id === event.target.value);
            patch({packId: event.target.value, order: picked ? [...picked.assetIds] : [],
              ...(picked?.kind ? {defaultKind: picked.kind} : {})});
          }}>
            <option value="">不绑定默认包（调用时按用途取图）</option>
            {packs.map(pack => <option key={pack.id} value={pack.id}>{pack.label} · {pack.images} 张图</option>)}
          </select>
          {!form.packId && <span className="mt-1 block text-[11px] text-ink-mute">不绑包时只声明用途槽位：调用者选自己的素材包，按「正面 / 三视图 / 细节」取图，编号不变。需要模板已声明用途绑定（复制固定蓝本即可带上）。</span>}
        </label>
        {usesBindings ? <div className="space-y-1 text-xs sm:col-span-2" aria-label="用途槽位">
          <span>用途槽位（编号固定：{'{{Mixed 1}}'} 是原片，其余按下面的用途在调用时取图）</span>
          <ol className="mt-1 space-y-1">
            <li className="flex items-center gap-2 rounded-lg border border-line bg-panel-soft p-2">
              <span className="w-24 shrink-0 font-mono text-[11px] text-ink-mute">{'{{Mixed 1}}'}</span>
              <span className="min-w-0 flex-1 truncate text-ink-soft">{form.sourceVideoName || '原片（固定第 1 位）'}</span>
              <span className="shrink-0 text-[11px] text-ink-mute">原片</span>
            </li>
            {slotRoles.map((role, index) => <li key={`${role}-${index}`} className="flex items-center gap-2 rounded-lg border border-line bg-panel p-2">
              <span className="w-24 shrink-0 font-mono text-[11px] text-ink-mute">{`{{Mixed ${index + 2}}}`}</span>
              <select aria-label={`{{Mixed ${index + 2}}} 用途`} className="min-w-0 flex-1 rounded border border-line bg-panel px-1.5 py-1 text-[11px]"
                value={role} onChange={event => patch({rules: withSlots(form, slotRoles.map((item, position) => position === index ? event.target.value : item))})}>
                {Object.entries(slotLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
              <button type="button" aria-label={`删除 {{Mixed ${index + 2}}}`} className="shrink-0 rounded px-1 text-ink-mute"
                onClick={() => patch({rules: withSlots(form, slotRoles.filter((_, position) => position !== index))})}>×</button>
            </li>)}
          </ol>
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" type="button" onClick={() => patch({rules: withSlots(form, [...slotRoles, baseRole(form.defaultKind)])})}>加一个用途槽位</Button>
            <Button size="sm" type="button" variant="ghost" onClick={() => patch({rules: withoutSlots(form)})}>改回按包内顺序</Button>
          </div>
          <span className="block text-[11px] text-ink-mute">不绑默认包时，调用者选自己的包，按这些用途取图；编号不变。</span>
        </div> : <div className="text-xs sm:col-span-2" aria-label="媒体顺序">
          <span>媒体顺序（决定提示词里的编号指向哪张图）</span>
          <ol className="mt-1 space-y-1">
            <li className="flex items-center gap-2 rounded-lg border border-line bg-panel-soft p-2">
              <span className="w-24 shrink-0 font-mono text-[11px] text-ink-mute">{'{{Mixed 1}}'}</span>
              <span className="min-w-0 flex-1 truncate text-ink-soft">{form.sourceVideoName || '原片（固定第 1 位）'}</span>
              <span className="shrink-0 text-[11px] text-ink-mute">原片</span>
            </li>
            {form.order.map((assetId, index) => {
              const pack = packs.find(item => item.id === form.packId);
              const position = (pack?.assetIds ?? []).indexOf(assetId) + 1;
              const preview = position > 0 ? pack?.previews?.[position - 1] : undefined;
              return <li key={assetId} className="flex items-center gap-2 rounded-lg border border-line bg-panel p-2">
                <span className="w-24 shrink-0 font-mono text-[11px] text-ink-mute">{`{{Mixed ${index + 2}}}`}</span>
                {preview && <img src={preview} alt="" className="h-8 w-8 shrink-0 rounded object-cover" />}
                <span className="min-w-0 flex-1 truncate text-ink-soft">包内第 {position || '?'} 张</span>
                <button type="button" aria-label={`上移第 ${index + 2} 位`} className="shrink-0 rounded px-1 text-ink-mute disabled:opacity-30"
                  disabled={index === 0} onClick={() => patch({order: move(form.order, index, index - 1)})}>↑</button>
                <button type="button" aria-label={`下移第 ${index + 2} 位`} className="shrink-0 rounded px-1 text-ink-mute disabled:opacity-30"
                  disabled={index === form.order.length - 1} onClick={() => patch({order: move(form.order, index, index + 1)})}>↓</button>
              </li>;
            })}
          </ol>
          <span className="mt-1 block text-[11px] text-ink-mute">默认沿用素材包顺序；只有这里的顺序会进接口，素材包本身不改。</span>
          <Button size="sm" type="button" variant="ghost" className="mt-1" onClick={() => patch({rules: withSlots(form, [baseRole(form.defaultKind)]), order: []})}>改为按用途声明槽位</Button>
        </div>}
        </>}
        <label className="block text-xs sm:col-span-2">样片原片（绑定后才是「全部模板」里的可选卡片）
          <select className={input} value={form.sourceVideoAssetId} required onChange={event => {
            const picked = sources.find(source => source.id === event.target.value);
            patch({sourceVideoAssetId: event.target.value,
              ...(picked ? {sourceVideoName: picked.name} : {}),
              ...(picked?.durationSec ? {sourceDurationSec: String(picked.durationSec)} : {})});
          }}>
            <option value="">请选择已登记的原片</option>
            {sources.map(source => <option key={source.id} value={source.id}>{source.label}</option>)}
          </select>
        </label>
        <label className="block text-xs">工作台功能名称<input className={input} value={form.workbenchLabel ?? ''} maxLength={80} onChange={event => patch({workbenchLabel: event.target.value})} placeholder="例如：外穿产品替换" /></label>
        {!form.textScene && <label className="block text-xs sm:col-span-2">替换范围选项（选填，每行一项）<textarea className={input} rows={3} value={form.scopeOptionsText ?? ''} onChange={event => patch({scopeOptionsText: event.target.value})} placeholder="留空则工作台不显示范围选择" /><span className="block text-ink-mute">范围已写在提示词中时留空；需要用户选择时，列出选项并在提示词中放入 {'{{ReplacementScope}}'}。</span></label>}
        <label className="block text-xs">默认替换类型<select className={input} value={form.defaultKind} disabled={form.textScene} onChange={event => patch({defaultKind: event.target.value as FormState['defaultKind']})}>{KINDS.map(kind => <option key={kind.value} value={kind.value}>{kind.label}</option>)}</select></label>
        <label className="block text-xs">样片文件名<input className={input} value={form.sourceVideoName} onChange={event => patch({sourceVideoName: event.target.value})} placeholder="原片视频文件名" /></label>
        <label className="block text-xs">样片本地路径（可选）<input className={input} value={form.sourceVideoPath} onChange={event => patch({sourceVideoPath: event.target.value})} placeholder="D:/.../样片.mp4" /></label>
        <label className="block text-xs">样片时长（秒）<input className={input} type="number" min="0" step="any" value={form.sourceDurationSec} onChange={event => patch({sourceDurationSec: event.target.value})} placeholder="5.31" /></label>
        <label className="block text-xs">输出画幅<select className={input} value={form.aspectRatio} onChange={event => patch({aspectRatio: event.target.value})}>{ASPECTS.map(aspect => <option key={aspect} value={aspect}>{aspect}</option>)}</select></label>
        <label className="block text-xs sm:col-span-2">模板说明<input className={input} value={form.description} onChange={event => patch({description: event.target.value})} placeholder="这条模板用于什么场景" /></label>
      </div>
      <label className="block text-xs">固定提示词
        <textarea className={`${input} h-40 font-mono leading-relaxed`} value={form.prompt} onChange={event => patch({prompt: event.target.value})} placeholder={'对输入的原视频 {{Mixed 1}} 进行局部替换，目标外观由 {{Mixed 2}} 中的款式决定。'} />
        <span className="mt-1 block text-[11px] text-ink-mute">{form.textScene ? '请在固定提示词放入一个 {{ScenePrompt}}，工作台仅替换这段描述。' : form.packId ? <>当前素材包可用编号：{'{{Mixed 1}}'} 原片，{'{{Mixed 2}}'} 起为上面「媒体顺序」里的图片，最大 {`{{Mixed ${1 + images}}}`}。</> : <>按用途声明的编号：{'{{Mixed 1}}'} 原片，{'{{Mixed 2}}'} 起为用途槽位，最大 {`{{Mixed ${Math.max(1, Object.keys(form.rules?.referenceBindings ?? {}).length)}}}`}。</>}</span>
      </label>
      <label className="flex items-center gap-2 text-xs text-ink-soft"><input type="checkbox" checked={form.pinned} onChange={event => patch({pinned: event.target.checked})} />设为固定蓝本（全库仅一个，后续可一键复制）</label>
      {error && <p role="alert" className="rounded-lg bg-danger/5 p-2 text-xs text-danger">{error}</p>}
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="primary" type="submit" loading={busy} disabled={busy}>保存封装</Button>
        <Button type="button" variant="ghost" disabled={busy} onClick={onClose}>取消</Button>
        <span className="text-xs text-ink-mute">保存后立即出现在模板库</span>
      </div>
    </form>
  </Modal>;
}

export function VideoTemplateLibrary({embedded = false}: {embedded?: boolean} = {}) {
  const query = useVideoTemplates();
  const mutations = useVideoTemplateMutations();
  const packsQuery = useAvailablePacks('all', '', 1);
  const sourcesQuery = useArchiveAssets('source', {scope: 'mine', pageSize: 100});
  const [editing, setEditing] = useState<FormState | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  const items = query.data?.items ?? [];
  const blueprint = items.find(item => item.pinned) ?? null;
  const packs: PackOption[] = (packsQuery.data?.items ?? []).map(pack => ({
    id: pack.id,
    kind: pack.kind,
    label: `${pack.name} v${pack.version}`,
    images: pack.referenceIds?.length ?? 0,
    assetIds: pack.referenceIds ?? [],
    previews: pack.previewUrls ?? [],
  }));
  const sources: SourceOption[] = (sourcesQuery.data?.items ?? [])
    .filter(asset => asset.category === 'source')
    .map(asset => ({
      id: asset.id,
      label: `${asset.name}（${asset.duration ?? 0}s）`,
      name: asset.name,
      durationSec: asset.duration,
    }));

  async function handleSubmit(payload: VideoTemplateDraftIn) {
    await mutations.create.mutateAsync(payload);
    toast('视频模板已封装', 'success');
    setEditing(null);
  }

  async function handlePin(id: string) {
    try {
      await mutations.pin.mutateAsync(id);
      toast('已设为固定蓝本', 'success');
    } catch (reason) {
      toast(reason instanceof Error ? reason.message : '设置失败', 'error');
    }
  }

  return <section className={embedded ? 'mt-3 space-y-3' : 'mb-6 space-y-3 rounded-xl border border-line bg-panel p-4'}>
    <header className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h2 className="text-sm font-semibold text-ink">视频模板</h2>
        <p className="mt-1 text-xs text-ink-mute">固定样片 + 功能提示词，可使用素材包或文字场景描述。固定蓝本可一键复制，用于封装后续模板。</p>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" disabled={!blueprint} onClick={() => blueprint && setEditing(formFromBlueprint(blueprint))}>复制固定蓝本</Button>
        <Button size="sm" variant="primary" onClick={() => setEditing(emptyForm())}>新建封装</Button>
      </div>
    </header>

    {query.error && <ErrorState error={query.error} title="视频模板读取失败" onRetry={() => {void query.refetch();}} />}
    {query.isPending && <p role="status" className="text-xs">正在读取视频模板…</p>}
    {!query.error && !query.isPending && !items.length &&
      <EmptyState title="还没有视频模板" description="点击「新建封装」创建第一条，或先发布一个替换素材包。" />}

    <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
      {items.map(template => {
        const media = template.mediaOrder ?? [];
        const images = media.filter(item => item.type === 'image');
        const referenced = media.filter(item => item.inPrompt).map(item => item.token);
        const open = expanded === template.id;
        return <article key={template.id} className="space-y-2 rounded-lg border border-line bg-panel-soft p-3">
          <div className="flex items-start justify-between gap-2">
            <h3 className="text-xs font-semibold text-ink">{template.name}</h3>
            {template.pinned && <span className="shrink-0 rounded bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">固定蓝本</span>}
          </div>
          <p className="text-[11px] text-ink-mute">{template.rules?.inputMode === 'video_scene_text' ? '文字换场景 · 仅原片' : template.packId ? `${template.packName} v${template.packVersion}` : '按用途取图 · 调用时选包'} · {template.aspectRatio} · {String(template.generation?.duration ?? '—')} 秒 · {images.length} 张图</p>
          <p className="text-[11px] text-ink-soft">{template.sourceVideoName || '未登记样片'}</p>
          <p className={template.sourceVideoAssetId ? 'text-[11px] text-primary' : 'text-[11px] text-danger'}>
            {template.sourceVideoAssetId ? `已绑定原片卡片 · 默认${KINDS.find(kind => kind.value === template.defaultKind)?.label ?? '产品替换'}` : '未绑定原片，不会出现在「全部模板」'}
          </p>
          <p className="text-[11px] text-ink-mute">提示词引用：{referenced.join('、') || '—'}</p>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" onClick={() => setExpanded(open ? null : template.id)}>{open ? '收起详情' : '查看提示词与顺序'}</Button>
            <Button size="sm" variant="ghost" disabled={template.pinned || mutations.pin.isPending} onClick={() => {void handlePin(template.id);}}>设为固定</Button>
          </div>
          {open && <div className="space-y-2 border-t border-line pt-2">
            <p className="text-[11px] font-semibold text-ink">{template.rules?.inputMode === 'video_scene_text' ? '场景描述参数 + 固定保留约束' : '你写的固定提示词（调用时逐字提交，不改写）'}</p>
            <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded bg-panel p-2 text-[11px] leading-relaxed text-ink-soft">{template.prompt}</pre>
            <ol className="space-y-1">
              {media.map(item => <li key={item.token} className="flex flex-wrap gap-1.5 text-[11px] text-ink-soft">
                <span className="font-mono text-primary">{item.token}</span>
                <span>{item.type} · {item.role}</span>
                <span className="truncate">{item.name || '—'}</span>
                <span className={item.inPrompt ? 'text-primary' : 'text-ink-mute'}>{item.inPrompt ? '提示词引用' : '仅插入接口'}</span>
              </li>)}
            </ol>
            <p className="text-[10px] text-ink-mute">提示词 sha256：{template.promptSha256}</p>
          </div>}
        </article>;
      })}
    </div>

    {editing && <VideoTemplateEditor
      initial={editing}
      packs={packs}
      sources={sources}
      busy={mutations.create.isPending}
      onClose={() => setEditing(null)}
      onSubmit={handleSubmit}
    />}
  </section>;
}
