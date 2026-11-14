import { useMemo, useRef, useState, type ReactNode } from 'react';
import {
  ChevronLeft,
  GripVertical,
  ImageOff,
  ImagePlus,
  Plus,
  RotateCcw,
  Trash2,
  Upload,
} from 'lucide-react';
import { useModels } from '@/entities/model/api/model.queries';
import { ParamFields } from '@/entities/model/ui/ParamFields';
import {
  paramControlsFor,
  resolveParamValues,
  type ParamValue,
} from '@/entities/model/lib/paramForm';
import { libraryApi } from '@/entities/library/api/library.api';
import { useLibraryDetail, useLibraryTags } from '@/entities/library/api/library.queries';
import { KIND_OPTIONS, viewQuery } from '@/entities/library/model/types';
import { AssetChip } from '@/entities/library/ui/AssetChip';
import { CaseCover } from '@/entities/library/ui/CaseCover';
import { PackChip } from '@/entities/library/ui/PackChip';
import { AssetPreview } from '@/entities/library/ui/AssetPreview';
import {
  useSpecMutations,
  useSpecs,
  useTrashedSpecs,
} from '@/entities/template-spec/api/spec.queries';
import type { SpecSlotIn, TemplateSpecIn, TemplateSpecOut } from '@/shared/api/schema';
import { Button, Chip, EmptyState, FormField, Select, TextInput } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import { AssetPicker, type PickKind, type PickedAsset } from '@/features/template-specs/AssetPicker';
import {
  SLOT_TYPE_LABELS,
  blankSlot,
  blankSpec,
  fromSpecOut,
  insertRef,
  normalize,
  referenceChips,
  rekeySlot,
  stripSlotRefs,
} from './specDraft';

/* 类型 = **资产库的 kind**：槽位要什么，就是库里有的那几类（图片 / 成片 / 音频 /
   文本 / 封装）。不另写一份本地词表 —— 库里多一类，这里就多一档。 */
const TYPE_OPTIONS = KIND_OPTIONS.filter(item => item.value !== 'all');

/** 一级标签的「不限」哨兵：空串表示候选集不收窄。 */
const TAG_ANY = '';

const BINDING_OPTIONS = [
  {value: 'user', label: '使用时填'},
  {value: 'fixed', label: '封装时固定'},
];

const STATUS_LABELS: Record<string, string> = {
  draft: '草稿',
  published: '已发布',
  disabled: '已停用',
};

/** 槽位拖拽中的下标；用 key 定位比下标稳（重排过程中下标会变）。 */
type DragState = {key: string} | null;

/** 素材选择器的目标：某个槽位，或展示案例。 */
type PickTarget = {target: 'slot'; index: number} | {target: 'demo'; kind: PickKind};

export function SpecWorkspace() {
  const list = useSpecs();
  const trash = useTrashedSpecs();
  const {validate, save, publish, disable, remove, restore} = useSpecMutations();
  // 规格可以指向视频模型，也可以指向图片模型；两组的参数区都由服务端 schema 驱动。
  const videoModels = useModels('video');
  const imageModels = useModels('image');
  const models = useMemo(() => [...videoModels, ...imageModels], [videoModels, imageModels]);

  const [draft, setDraft] = useState<TemplateSpecIn | null>(null);
  const [view, setView] = useState<'list' | 'trash'>('list');
  const [note, setNote] = useState('');
  const [error, setError] = useState('');
  const [hint, setHint] = useState('');
  const [picker, setPicker] = useState<PickTarget | null>(null);
  const [uploading, setUploading] = useState(false);
  const [drag, setDrag] = useState<DragState>(null);
  const [caret, setCaret] = useState<number | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const busy =
    validate.isPending ||
    save.isPending ||
    publish.isPending ||
    disable.isPending ||
    remove.isPending ||
    restore.isPending ||
    uploading;
  // 契约里 `slots` 是可选的（后端有默认空列表），编辑器一律按非空处理。
  const slots = draft?.slots ?? [];
  const pickerSlot = picker?.target === 'slot' ? slots[picker.index] : undefined;

  /* 一级标签：资产库的两级词表，槽位只用**一级**（二级是同一句话的细分，
     拿来当候选集范围反而让人选不出来）。数量按全库算，跟槽位类型无关 ——
     这里的用途是「挑范围」，不是「看有多少条」。 */
  const tags = useLibraryTags(viewQuery('all'));
  const tagOptions = useMemo(
    () => [
      {value: TAG_ANY, label: '不限'},
      ...(tags.data ?? [])
        .filter(tag => (tag.depth ?? (tag.parentId ? 2 : 1)) === 1)
        .map(tag => ({value: tag.id, label: tag.name})),
    ],
    [tags.data],
  );

  const clear = () => {
    setError('');
    setNote('');
    setHint('');
  };

  const fail = (reason: unknown) =>
    setError(reason instanceof Error ? reason.message : String(reason));

  const submit = async (action: 'validate' | 'save' | 'publish') => {
    if (!draft) return;
    clear();
    try {
      const payload = normalize(draft);
      if (action === 'validate') {
        const result = await validate.mutateAsync(payload);
        setNote(`预检通过 · 指纹 ${result.fingerprint.slice(0, 12)}`);
        return;
      }
      const saved = await save.mutateAsync(payload);
      setDraft(fromSpecOut(saved));
      if (action === 'save') {
        setNote(`已保存（第 ${saved.revision} 次修订）`);
        return;
      }
      const receipt = await publish.mutateAsync(saved.key);
      setDraft(fromSpecOut(receipt.spec));
      setNote(`已发布 v${receipt.version}`);
    } catch (reason) {
      fail(reason);
    }
  };

  const patchSlot = (index: number, patch: Partial<SpecSlotIn>) => {
    if (!draft) return;
    const nextSlots = slots.slice();
    const next = {...nextSlots[index], ...patch};
    // 音频/文本一律是附加输入：类型一改就同步，别让用户先撞一次 400 再回来改。
    if (next.type === 'audio' || next.type === 'text') next.refMode = 'attachment';
    if (next.type === 'image' || next.type === 'video' || next.type === 'pack') {
      next.refMode = 'mixed';
    }
    // 封装槽位：成员与顺序由资产库决定，所以数量恒为 1/1、不能绑单个素材。
    if (next.type === 'pack') {
      next.assetId = null;
      next.minCount = 1;
      next.maxCount = 1;
    } else {
      next.packId = null;
    }
    nextSlots[index] = next;
    // 改类型时跟着改名（只在提示词没引用过时；引用过就留着，别让引用静默失效）。
    const rekeyed =
      patch.type !== undefined
        ? rekeySlot(nextSlots, index, next.type, draft.prompt ?? '')
        : nextSlots;
    setDraft({...draft, slots: rekeyed});
  };

  const moveSlot = (from: number, to: number) => {
    if (!draft || from === to) return;
    const next = slots.slice();
    const [item] = next.splice(from, 1);
    next.splice(to, 0, item);
    setDraft({...draft, slots: next.map((slot, index) => ({...slot, position: index}))});
  };

  const addSlot = (type: SpecSlotIn['type'] = 'image') => {
    if (!draft) return;
    const slot = blankSlot(slots.length, type, slots);
    setDraft({...draft, slots: [...slots, slot]});
    if (type === 'text') setPicker({target: 'slot', index: slots.length});
  };

  const pick = (asset: PickedAsset) => {
    if (!draft || !picker) return;
    if (picker.target === 'demo') {
      setDraft({...draft, demoAssetId: asset.id});
    } else {
      const id = picker.index;
      const slot = slots[id];
      patchSlot(
        id,
        slot.type === 'pack'
          ? {
              packId: asset.id,
              assetId: null,
              label: slot.label?.trim() ? slot.label : asset.name,
            }
          : {
              assetId: asset.id,
              packId: null,
              label: slot.label?.trim() ? slot.label : asset.name,
            },
      );
    }
    setPicker(null);
  };

  const uploadDemo = async (file: File) => {
    if (!draft) return;
    clear();
    setUploading(true);
    try {
      const asset = file.type.startsWith('video/')
        ? await libraryApi.uploadSourceVideo(file)
        : await libraryApi.upload(file, 'image');
      setDraft(current => (current ? {...current, demoAssetId: asset.id} : current));
    } catch (reason) {
      fail(reason);
    } finally {
      setUploading(false);
    }
  };

  const writeRef = (token: string) => {
    if (!draft) return;
    const at = caret ?? (draft.prompt ?? '').length;
    const result = insertRef(draft.prompt ?? '', token, at);
    setDraft({...draft, prompt: result.text});
    setCaret(result.caret);
    setHint('');
  };

  /**
   * 提示词只让改**描述文字**，引用一律从按钮插入。
   *
   * 手写 `{{slot:x}}` 会在两处出错且都不报错：拼错 key → 编译期才 400；手写
   * `{{Mixed N}}` → 数字与本次填充量对不上，少填一张就整体错位。所以这里直接
   * 拒绝新增花括号：要么删字，要么用下面的按钮。
   */
  const setPromptText = (text: string) => {
    if (!draft) return;
    const before = draft.prompt ?? '';
    const braces = (value: string) => (value.match(/[{}]/g) ?? []).length;
    if (braces(text) > braces(before)) {
      setHint('引用请用下方按钮插入，不要手写大括号');
      return;
    }
    setHint('');
    setDraft({...draft, prompt: text});
  };

  const chips = draft ? referenceChips(draft) : [];

  const moveToTrash = (item: TemplateSpecOut) => {
    if (!window.confirm(`把「${item.name}」移进回收站？可以再恢复。`)) return;
    clear();
    remove.mutateAsync(item.key).then(() => setNote(`已移进回收站：${item.name}`), fail);
  };

  /* ---------- 列表 / 回收站 ---------- */
  if (!draft) {
    const trashed = view === 'trash';
    const rows = (trashed ? trash.data?.items : list.data?.items) ?? [];
    const loading = trashed ? trash.isLoading : list.isLoading;
    return (
      <section className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-0.5 rounded-full border border-line bg-panel p-0.5">
            {(
              [
                ['list', '模板'],
                ['trash', '回收站'],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                type="button"
                onClick={() => {
                  setView(value);
                  clear();
                }}
                className={cn(
                  'rounded-full px-3 py-1 text-[11px] transition-colors',
                  view === value
                    ? 'bg-primary/10 font-medium text-primary'
                    : 'text-ink-mute hover:text-ink',
                )}
              >
                {label}
                {value === 'trash' && (trash.data?.total ?? 0) > 0 && (
                  <span className="ml-1 text-ink-mute">{trash.data?.total}</span>
                )}
              </button>
            ))}
          </div>
          {!trashed && (
            <Button onClick={() => setDraft(blankSpec())}>
              <Plus size={14} /> 新建模板
            </Button>
          )}
        </div>

        {(note || error) && <Notice error={error} note={note} />}
        {loading ? (
          <p className="py-10 text-center text-xs text-ink-mute">正在读取…</p>
        ) : rows.length === 0 ? (
          <EmptyState
            title={trashed ? '回收站是空的' : '还没有模板'}
            description={
              trashed
                ? '删除的模板会先放这里，随时可以恢复。'
                : '新建一份模板：选模型、摆槽位、写提示词。'
            }
          />
        ) : (
          /* 卡片而不是表格行：一张模板的"长相"是它的案例封面，列表里也该看得见。
             操作放在卡片脚上，扫一眼就知道哪张能发布、哪张已停用。 */
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {rows.map(item => (
              <SpecCard
                key={item.id}
                item={item}
                busy={busy}
                trashed={trashed}
                onEdit={() => {
                  setDraft(fromSpecOut(item));
                  clear();
                }}
                onPublish={() =>
                  publish
                    .mutateAsync(item.key)
                    .then(result => setNote(`已发布 ${item.name} v${result.version}`))
                    .catch(fail)
                }
                onDisable={() =>
                  disable
                    .mutateAsync(item.key)
                    .then(() => setNote(`已停用 ${item.name}`))
                    .catch(fail)
                }
                onRemove={() => moveToTrash(item)}
                onRestore={() =>
                  restore
                    .mutateAsync(item.key)
                    .then(() => setNote(`已恢复 ${item.name}`))
                    .catch(fail)
                }
              />
            ))}
          </div>
        )}
      </section>
    );
  }

  /* ---------- 编辑器 ---------- */
  /* 参数控件由模型声明驱动（中文标签、档位文案都在 `paramControlsFor` 里）：
     以前这里拿 `Object.entries(paramSchema)` 直接渲染，界面上印的是 `durationSec`
     这种英文 key，枚举值也是裸的 `9:16`。 */
  const paramControls = paramControlsFor(models.find(item => item.id === draft.model));
  const paramOverrides: Record<string, ParamValue> = {};
  for (const [key, value] of Object.entries(draft.params ?? {})) {
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
      paramOverrides[key] = value;
    }
  }
  const paramValues = resolveParamValues(paramControls, paramOverrides);
  return (
    <section className="flex flex-col gap-4">
      {/* 操作栏吸顶：槽位一多就要滚很久，保存/发布不该被滚出屏幕。
          `top-[54px]` 让开顶栏那条同为 sticky 的 header，否则会被它盖住。 */}
      <div className="sticky top-[54px] z-20 -mx-1 flex flex-wrap items-center justify-between gap-3 border-b border-line-soft bg-panel px-1 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <button
            type="button"
            className="flex shrink-0 items-center gap-1 rounded-lg border border-line-soft bg-panel px-2 py-1 text-[11px] text-ink-soft transition-colors hover:border-line hover:text-ink"
            onClick={() => {
              setDraft(null);
              clear();
            }}
          >
            <ChevronLeft size={12} /> 列表
          </button>
          <span className="min-w-0 truncate text-[13.5px] font-medium text-ink">
            {draft.name || '未命名模板'}
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          <Button variant="ghost" disabled={busy} onClick={() => void submit('validate')}>
            预检
          </Button>
          <Button variant="ghost" disabled={busy} onClick={() => void submit('save')}>
            保存草稿
          </Button>
          <Button disabled={busy} onClick={() => void submit('publish')}>
            保存并发布
          </Button>
        </div>
      </div>

      {(note || error) && <Notice error={error} note={note} />}

      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]">
        <div className="flex flex-col gap-4">
          <Section tone="primary" title="基本信息">
            <div className="grid gap-3 sm:grid-cols-[1.4fr_1fr]">
              <FormField label="名称" required>
                <TextInput
                  value={draft.name}
                  placeholder="例如：米兰之夜 · 产品替换"
                  onChange={event => setDraft({...draft, name: event.target.value})}
                />
              </FormField>
              <FormField label="模型" required>
                <Select
                  block
                  value={draft.model}
                  options={[
                    {value: '', label: '选择一个模型'},
                    ...models.map(item => ({value: item.id, label: item.label})),
                  ]}
                  onChange={event => setDraft({...draft, model: event.target.value, params: {}})}
                />
              </FormField>
            </div>
            {paramControls.length > 0 && (
              <ParamFields
                className="mt-3"
                idPrefix="spec-param"
                controls={paramControls}
                values={paramValues}
                onChange={(key, value) =>
                  setDraft({...draft, params: {...draft.params, [key]: value}})
                }
              />
            )}
          </Section>

          <Section
            tone="violet"
            title="槽位"
            extra={
              /* 「加槽位」也按资产库的 kind 生成：库里多一类，这里就多一个入口，
                 和槽位卡片里的「类型」下拉同源（曾经这里写死四个按钮）。 */
              <div className="flex flex-wrap items-center gap-0.5 rounded-full border border-line-soft bg-panel-soft p-0.5">
                {TYPE_OPTIONS.map(item => (
                  <button
                    key={item.value}
                    type="button"
                    onClick={() => addSlot(item.value as SpecSlotIn['type'])}
                    className="flex items-center gap-0.5 rounded-full px-2 py-[3px] text-[11px] text-ink-soft transition-colors hover:bg-panel hover:text-primary"
                  >
                    <Plus size={11} /> {item.label}
                  </button>
                ))}
              </div>
            }
          >
            <div className="flex flex-col gap-2">
              {slots.length === 0 && (
                <p className="rounded-xl border border-dashed border-line-strong bg-panel-soft px-3 py-4 text-[11px] text-ink-mute">
                  没有槽位。纯提示词的模板不需要素材，从上面的按钮加一个就行。
                </p>
              )}
              {slots.map((slot, index) => {
                const fixed = (slot.binding ?? 'user') === 'fixed';
                return (
                  <div
                    key={slot.key}
                    onDragOver={event => drag && event.preventDefault()}
                    onDrop={event => {
                      if (!drag) return;
                      event.preventDefault();
                      const from = slots.findIndex(item => item.key === drag.key);
                      setDrag(null);
                      if (from >= 0) moveSlot(from, index);
                    }}
                    className={cn(
                      'rounded-xl border p-3 transition-colors',
                      fixed
                        ? 'border-ok-line bg-ok-soft/50'
                        : 'border-line-soft bg-panel-soft hover:border-line',
                      drag?.key === slot.key && 'border-primary-line bg-primary-soft/60',
                    )}
                  >
                    <div className="mb-2 flex items-center justify-between">
                      <span
                        draggable
                        onDragStart={() => setDrag({key: slot.key})}
                        onDragEnd={() => setDrag(null)}
                        className="flex cursor-grab items-center gap-1.5 text-[11px] text-ink-mute active:cursor-grabbing"
                        title="拖动调整顺序（顺序即编号）"
                      >
                        <GripVertical size={13} />
                        <span className="flex h-[18px] w-[18px] items-center justify-center rounded-full bg-primary text-[10px] font-semibold text-white shadow-[0_1px_3px_rgba(43,124,255,.35)]">
                          {index + 1}
                        </span>
                        <span className="text-[10.5px] text-ink-mute">
                          {SLOT_TYPE_LABELS[slot.type]}
                        </span>
                      </span>
                      <button
                        type="button"
                        className="rounded-md p-1 text-ink-mute transition-colors hover:bg-danger-soft hover:text-danger"
                        title="删除槽位"
                        onClick={() => {
                          const name = slot.label?.trim() || SLOT_TYPE_LABELS[slot.type];
                          // 引用留着的下场是「点生成才报引用了不存在的槽位」，
                          // 那句话看不出是刚才删槽位删出来的，所以一并清掉并告知条数。
                          const stripped = stripSlotRefs(draft.prompt ?? '', slot.key);
                          setDraft({
                            ...draft,
                            slots: slots.filter(item => item.key !== slot.key),
                            prompt: stripped.text,
                          });
                          setNote(
                            stripped.count > 0
                              ? `已删除槽位「${name}」，提示词里的 ${stripped.count} 处引用一并清掉`
                              : `已删除槽位「${name}」`,
                          );
                        }}
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                    <div className="grid gap-2 sm:grid-cols-[1.6fr_1fr_1fr_1fr]">
                      <FormField label="展示名">
                        <TextInput
                          value={slot.label ?? ''}
                          placeholder={`${SLOT_TYPE_LABELS[slot.type]} ${index + 1}`}
                          onChange={event => patchSlot(index, {label: event.target.value})}
                        />
                      </FormField>
                      <FormField label="类型">
                        <Select
                          block
                          value={slot.type}
                          options={TYPE_OPTIONS}
                          onChange={event =>
                            patchSlot(index, {type: event.target.value as SpecSlotIn['type']})
                          }
                        />
                      </FormField>
                      <FormField label="一级标签">
                        <Select
                          block
                          value={slot.tagId ?? TAG_ANY}
                          options={tagOptions}
                          onChange={event =>
                            patchSlot(index, {tagId: event.target.value})
                          }
                        />
                      </FormField>
                      <FormField label="谁定">
                        <Select
                          block
                          value={slot.binding ?? 'user'}
                          options={BINDING_OPTIONS}
                          onChange={event =>
                            patchSlot(index, {
                              binding: event.target.value as SpecSlotIn['binding'],
                            })
                          }
                        />
                      </FormField>
                    </div>
                    <div className="mt-2 flex flex-wrap items-end gap-2">
                      <FormField label="进提示词的用途说明" className="min-w-[220px] flex-1">
                        <TextInput
                          value={slot.usage ?? ''}
                          placeholder="例如：确定同款同色外观；只补纹理与扣件"
                          onChange={event => patchSlot(index, {usage: event.target.value})}
                        />
                      </FormField>
                      {slot.type !== 'pack' &&
                        (slot.binding ?? 'user') === 'user' &&
                        slot.type !== 'text' && (
                          <>
                            <FormField label="最少">
                              <TextInput
                                value={String(slot.minCount ?? 1)}
                                onChange={event =>
                                  patchSlot(index, {minCount: Number(event.target.value || 0)})
                                }
                                className="w-[58px] text-center"
                              />
                            </FormField>
                            <FormField label="最多">
                              <TextInput
                                value={String(slot.maxCount ?? 1)}
                                onChange={event =>
                                  patchSlot(index, {maxCount: Number(event.target.value || 1)})
                                }
                                className="w-[58px] text-center"
                              />
                            </FormField>
                          </>
                        )}
                      {fixed && (
                        <Button
                          variant="secondary"
                          onClick={() => setPicker({target: 'slot', index})}
                        >
                          {slot.type === 'pack' ? '选择封装' : '选择素材'}
                        </Button>
                      )}
                    </div>
                    {/* 固定槽位已经选好了东西：显示它是什么，而不是一串 id——
                        那串 id 对作者没有任何信息量，还会被当成"是不是选错了"。 */}
                    {fixed && (
                      <div className="mt-2 flex flex-wrap items-center gap-1.5">
                        {slot.type === 'pack' ? (
                          slot.packId ? (
                            <PackChip packId={slot.packId} />
                          ) : (
                            <span className="text-[11px] text-ink-mute">还没选封装</span>
                          )
                        ) : slot.assetId ? (
                          <AssetChip assetId={slot.assetId} />
                        ) : (
                          <span className="text-[11px] text-ink-mute">还没选素材</span>
                        )}
                      </div>
                    )}
                    {(slot.refMode ?? 'mixed') === 'attachment' && slot.type === 'text' && (
                      <p className="mt-1 text-[11px] text-ink-mute">
                        文本槽位：占 {`{{Text N}}`} 的第 N 位，正文由所选文案注入
                      </p>
                    )}
                    {(slot.refMode ?? 'mixed') === 'attachment' && slot.type === 'audio' && (
                      <p className="mt-1 text-[11px] text-ink-mute">
                        附加输入：不占编号，提示词里不引用它
                      </p>
                    )}
                  </div>
                );
              })}
            </div>
          </Section>
        </div>

        <div className="flex flex-col gap-4 lg:sticky lg:top-[112px]">
          <Section
            tone="ok"
            title="展示案例"
            extra={
              <>
                <Button variant="ghost" size="sm" disabled={busy} onClick={() => fileRef.current?.click()}>
                  <Upload size={12} /> 上传
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={busy}
                  onClick={() => setPicker({target: 'demo', kind: 'image'})}
                >
                  <ImagePlus size={12} /> 选图片
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={busy}
                  onClick={() => setPicker({target: 'demo', kind: 'video'})}
                >
                  选视频
                </Button>
              </>
            }
          >
            <DemoCase
              assetId={draft.demoAssetId ?? null}
              onClear={() => setDraft({...draft, demoAssetId: null})}
            />
            <input
              ref={fileRef}
              type="file"
              accept="image/*,video/*"
              className="hidden"
              onChange={event => {
                const file = event.target.files?.[0];
                event.target.value = '';
                if (file) void uploadDemo(file);
              }}
            />
          </Section>

          <Section
            tone="primary"
            title="提示词"
            extra={
              <Button variant="ghost" size="sm" onClick={() => addSlot('text')}>
                <Plus size={12} /> 文本素材
              </Button>
            }
          >
            <PromptBox
              value={draft.prompt ?? ''}
              chips={chips}
              onRef={writeRef}
              onCaret={setCaret}
              onChange={setPromptText}
            />
            {hint && <p className="mt-2 text-[11px] text-warn-deep">{hint}</p>}
          </Section>
        </div>
      </div>

      <AssetPicker
        open={picker !== null}
        kind={picker?.target === 'demo' ? picker.kind : pickerSlot?.type ?? 'image'}
        /* 一级标签是槽位的候选集范围：封装页挑固定素材时也要收窄，
           否则作者能选到模板使用者那边根本看不到的东西。 */
        tagIds={picker?.target === 'slot' && pickerSlot?.tagId ? [pickerSlot.tagId] : []}
        selected={
          picker === null
            ? []
            : picker.target === 'demo'
              ? [draft.demoAssetId ?? ''].filter(Boolean)
              : [slots[picker.index].assetId ?? ''].filter(Boolean)
        }
        onClose={() => setPicker(null)}
        onToggle={pick}
      />
    </section>
  );
}

/** 展示案例：没设就明说没有，不要拿渐变块冒充缩略图。 */
function DemoCase({assetId, onClear}: {assetId: string | null; onClear: () => void}) {
  const detail = useLibraryDetail(assetId);
  if (!assetId) {
    return (
      <div className="flex h-40 flex-col items-center justify-center gap-2 rounded-xl border border-line-soft bg-panel-soft">
        <span className="grid size-9 place-items-center rounded-full bg-panel text-ink-mute ring-1 ring-inset ring-line-soft">
          <ImageOff size={15} />
        </span>
        <span className="text-[11px] text-ink-mute">暂无案例</span>
      </div>
    );
  }
  return (
    <div className="rounded-xl border border-line-soft bg-panel-soft p-2">
      {/* 视频只给封面 + 播放按钮，点开才取流；封面一直有，不会出现黑块。 */}
      <AssetPreview assetId={assetId} className="h-44 w-full rounded-lg" />
      <div className="mt-2 flex items-center justify-between gap-2 px-1">
        <p className="truncate text-[11px] text-ink-soft">
          {detail.data?.name ?? assetId.slice(0, 8)}
        </p>
        <button
          type="button"
          onClick={onClear}
          className="shrink-0 text-[11px] text-ink-mute transition-colors hover:text-danger"
        >
          移除案例
        </button>
      </div>
    </div>
  );
}

/** 分区卡片：左侧色条 + 标题，靠色彩分区而不是靠说明文字。 */
function Section({
  tone,
  title,
  hint,
  extra,
  children,
}: {
  tone: 'primary' | 'violet' | 'warn' | 'ok';
  title: string;
  hint?: string;
  extra?: ReactNode;
  children: ReactNode;
}) {
  const bar = {
    primary: 'bg-primary',
    violet: 'bg-violet',
    warn: 'bg-warn',
    ok: 'bg-ok',
  }[tone];
  return (
    <div className="rounded-2xl border border-line bg-panel p-4 shadow-[0_1px_2px_rgba(16,42,55,0.04)]">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-[12.5px] font-semibold tracking-[.2px] text-ink">
          <span className={cn('h-3.5 w-[3px] rounded-full', bar)} />
          {title}
          {hint && <span className="font-normal text-ink-mute">· {hint}</span>}
        </h3>
        {extra && <div className="flex flex-wrap items-center gap-1">{extra}</div>}
      </div>
      {children}
    </div>
  );
}

function Notice({ error, note }: {error: string; note: string}) {
  return (
    <div
      className={cn(
        'rounded-xl border px-3 py-2 text-[11px] leading-relaxed',
        error
          ? 'border-danger-line bg-danger-soft text-danger'
          : 'border-ok-line bg-ok-soft text-ok-deep',
      )}
    >
      <p className="whitespace-pre-wrap">{error || note}</p>
    </div>
  );
}

function PromptBox({
  value,
  chips,
  onRef,
  onCaret,
  onChange,
}: {
  value: string;
  chips: {token: string; label: string; all: boolean}[];
  onRef: (token: string) => void;
  onCaret: (at: number) => void;
  onChange: (text: string) => void;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  return (
    <div>
      <textarea
        ref={ref}
        value={value}
        rows={10}
        placeholder="写提示词；要可变的内容加一个文本槽位，再用下方按钮插入引用"
        onChange={event => {
          onChange(event.target.value);
          onCaret(event.target.selectionStart ?? 0);
        }}
        onSelect={event => onCaret(event.currentTarget.selectionStart ?? 0)}
        className={cn(
          'w-full resize-y rounded-xl border px-3 py-2.5 text-xs leading-[1.7] outline-none transition-colors',
          'border-line-soft bg-panel-soft text-ink placeholder:text-[#a9b8bf]',
          'hover:border-line focus:border-primary-line focus:bg-panel focus:ring-2 focus:ring-primary/10',
        )}
      />
      <div className="mt-2 flex flex-wrap gap-1.5">
        {chips.length === 0 ? (
          <span className="text-[11px] text-ink-mute">先在左边加槽位，再引用</span>
        ) : (
          chips.map(chip => (
            <button
              key={chip.token}
              type="button"
              onClick={() => onRef(chip.token)}
              className={cn(
                'rounded-full border px-2 py-0.5 text-[11px] transition-colors',
                chip.all
                  ? 'border-violet/40 bg-violet/5 text-violet hover:bg-violet/10'
                  : 'border-primary-line bg-panel text-ink-soft hover:border-primary hover:text-primary',
              )}
            >
              {chip.label}
            </button>
          ))
        )}
      </div>
    </div>
  );
}

/**
 * 模板卡片（列表页）。
 *
 * 用卡片而不是表格行：一张模板的"长相"是它的案例封面，表格里这条信息全丢了，
 * 一排十一行看起来像数据库导出。操作放在卡片脚上 —— 扫一眼就知道哪张能发布。
 */
function SpecCard({
  item,
  busy,
  trashed,
  onEdit,
  onPublish,
  onDisable,
  onRemove,
  onRestore,
}: {
  item: TemplateSpecOut;
  busy: boolean;
  trashed: boolean;
  onEdit: () => void;
  onPublish: () => void;
  onDisable: () => void;
  onRemove: () => void;
  onRestore: () => void;
}) {
  const slotCount = item.draft?.slots?.length ?? item.latest?.slots?.length ?? 0;
  const model = item.draft?.model || item.latest?.model || '';
  return (
    <div className="group flex flex-col overflow-hidden rounded-2xl border border-line-soft bg-panel transition-all duration-200 hover:border-line hover:shadow-card">
      <button
        type="button"
        onClick={onEdit}
        disabled={trashed}
        className="relative block w-full disabled:cursor-default"
      >
        <CaseCover
          assetId={item.demoAssetId}
          seed={item.key}
          className="aspect-[16/9] w-full"
          hoverPlay
        />
        <span className="absolute left-2 top-2">
          {trashed ? (
            <Chip tone="danger">回收站</Chip>
          ) : (
            <Chip
              tone={
                item.status === 'published' ? 'ok' : item.status === 'disabled' ? 'warn' : 'default'
              }
              className="backdrop-blur-sm"
            >
              {STATUS_LABELS[item.status] ?? item.status}
            </Chip>
          )}
        </span>
        {!trashed && item.currentVersion > 0 && (
          <span className="absolute right-2 top-2 rounded-full bg-black/40 px-1.5 py-0.5 text-[10px] font-medium leading-[14px] text-white/95 backdrop-blur-sm">
            v{item.currentVersion}
          </span>
        )}
      </button>

      <div className="flex min-w-0 flex-1 flex-col gap-0.5 px-3 pt-2.5">
        <button
          type="button"
          onClick={onEdit}
          disabled={trashed}
          className="truncate text-left text-[13px] font-medium text-ink transition-colors hover:text-primary disabled:cursor-default disabled:hover:text-ink"
        >
          {item.name}
        </button>
        <p className="truncate text-[11px] text-ink-mute">
          {trashed
            ? `删除于 ${item.deletedAt ? new Date(item.deletedAt).toLocaleString() : '—'}`
            : `${slotCount} 个槽位${model ? ` · ${model}` : ''}`}
        </p>
      </div>

      <div className="mt-2 flex items-center gap-3 border-t border-line-soft px-3 py-2 text-[11px]">
        {trashed ? (
          <button
            type="button"
            disabled={busy}
            onClick={onRestore}
            className="flex items-center gap-1 text-ink-soft transition-colors hover:text-primary disabled:opacity-50"
          >
            <RotateCcw size={11} /> 恢复
          </button>
        ) : (
          <>
            <button
              type="button"
              onClick={onEdit}
              className="font-medium text-primary transition-colors hover:text-primary-deep"
            >
              编辑
            </button>
            {item.status !== 'disabled' && (
              <button
                type="button"
                disabled={busy}
                onClick={onPublish}
                className="text-ink-soft transition-colors hover:text-primary disabled:opacity-50"
              >
                发布
              </button>
            )}
            {item.status === 'published' && (
              <button
                type="button"
                disabled={busy}
                onClick={onDisable}
                className="text-ink-soft transition-colors hover:text-warn-deep disabled:opacity-50"
              >
                停用
              </button>
            )}
            <button
              type="button"
              disabled={busy}
              onClick={onRemove}
              className="ml-auto text-ink-mute transition-colors hover:text-danger disabled:opacity-50"
            >
              删除
            </button>
          </>
        )}
      </div>
    </div>
  );
}
