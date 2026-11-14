import { useMemo, useRef, useState, type ReactNode } from 'react';
import { GripVertical, Plus, RotateCcw, Trash2 } from 'lucide-react';
import { useModels } from '@/entities/model/api/model.queries';
import {
  useSpecMutations,
  useSpecs,
  useTrashedSpecs,
} from '@/entities/template-spec/api/spec.queries';
import type { SpecSlotIn, TemplateSpecIn, TemplateSpecOut } from '@/shared/api/schema';
import { Button, Chip, EmptyState, FormField, Select, TextInput } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import { AssetPicker, type PickedAsset } from '@/features/template-specs/AssetPicker';
import {
  SLOT_TYPE_LABELS,
  blankSlot,
  blankSpec,
  fromSpecOut,
  insertRef,
  normalize,
  referenceChips,
} from './specDraft';

const TYPE_OPTIONS = (Object.keys(SLOT_TYPE_LABELS) as (keyof typeof SLOT_TYPE_LABELS)[]).map(
  value => ({value, label: SLOT_TYPE_LABELS[value]}),
);

const SOURCE_OPTIONS = [
  {value: 'asset', label: '单个资产'},
  {value: 'pack', label: '资产库封装'},
];

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
  const [picker, setPicker] = useState<number | null>(null);
  const [drag, setDrag] = useState<DragState>(null);
  const [caret, setCaret] = useState<{field: 'locked' | 'editable'; at: number} | null>(null);

  const busy =
    validate.isPending ||
    save.isPending ||
    publish.isPending ||
    disable.isPending ||
    remove.isPending ||
    restore.isPending;
  // 契约里 `slots` 是可选的（后端有默认空列表），编辑器一律按非空处理。
  const slots = draft?.slots ?? [];

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
    if (next.type === 'image' || next.type === 'video') next.refMode = 'mixed';
    if (next.type === 'text') next.source = 'asset';
    if (next.source === 'pack') {
      next.assetId = null;
      next.minCount = 1;
      next.maxCount = 1;
      if (next.type === 'text') next.type = 'image';
    } else {
      next.packId = null;
    }
    nextSlots[index] = next;
    setDraft({...draft, slots: nextSlots});
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
    if (type === 'text') setPicker(slots.length);
  };

  const pick = (asset: PickedAsset) => {
    if (!draft || picker === null) return;
    const slot = slots[picker];
    patchSlot(picker, {
      assetId: asset.id,
      packId: slot.source === 'pack' ? asset.id : null,
      label: slot.label?.trim() ? slot.label : asset.name,
    });
    setPicker(null);
  };

  const writeRef = (field: 'locked' | 'editable', token: string) => {
    if (!draft) return;
    const at = caret?.field === field ? caret.at : (draft.prompt[field] ?? '').length;
    const result = insertRef(draft.prompt[field] ?? '', token, at);
    setDraft({...draft, prompt: {...draft.prompt, [field]: result.text}});
    setCaret({field, at: result.caret});
    setHint('');
  };

  /**
   * 提示词只让改**描述文字**，引用一律从按钮插入。
   *
   * 手写 `{{slot:x}}` 会在两处出错且都不报错：拼错 key → 编译期才 400；手写
   * `{{Mixed N}}` → 数字与本次填充量对不上，少填一张就整体错位。所以这里直接
   * 拒绝新增花括号：要么删字，要么用下面的按钮。
   */
  const setPromptText = (field: 'locked' | 'editable', text: string) => {
    if (!draft) return;
    const before = draft.prompt[field] ?? '';
    const braces = (value: string) => (value.match(/[{}]/g) ?? []).length;
    if (braces(text) > braces(before)) {
      setHint('引用请用下方按钮插入，不要手写大括号');
      return;
    }
    setHint('');
    setDraft({...draft, prompt: {...draft.prompt, [field]: text}});
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
          <div className="overflow-hidden rounded-2xl border border-line bg-panel">
            {rows.map(item => (
              <SpecRow
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
  const paramFields = models.find(item => item.id === draft.model)?.paramSchema ?? {};
  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <button
            type="button"
            className="text-xs text-ink-mute hover:text-ink"
            onClick={() => {
              setDraft(null);
              clear();
            }}
          >
            ← 返回列表
          </button>
          <Chip>{draft.name || '未命名模板'}</Chip>
        </div>
        <div className="flex items-center gap-2">
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

      <div className="grid items-start gap-4 lg:grid-cols-[1.05fr_1fr]">
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
            {Object.keys(paramFields).length > 0 && (
              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                {Object.entries(paramFields).map(([name, field]) => (
                  <FormField key={name} label={name}>
                    {field.type === 'enum' ? (
                      <Select
                        block
                        value={String(draft.params?.[name] ?? field.default ?? '')}
                        options={(field.options ?? []).map(option => ({
                          value: String(option),
                          label: String(option),
                        }))}
                        onChange={event =>
                          setDraft({
                            ...draft,
                            params: {...draft.params, [name]: event.target.value},
                          })
                        }
                      />
                    ) : (
                      <TextInput
                        value={String(draft.params?.[name] ?? '')}
                        inputMode="numeric"
                        onChange={event =>
                          setDraft({
                            ...draft,
                            params: {
                              ...draft.params,
                              [name]:
                                event.target.value === '' ? undefined : Number(event.target.value),
                            },
                          })
                        }
                      />
                    )}
                  </FormField>
                ))}
              </div>
            )}
          </Section>

          <Section
            tone="violet"
            title="槽位"
            extra={
              <>
                <Button variant="ghost" onClick={() => addSlot('image')}>
                  <Plus size={13} /> 图片
                </Button>
                <Button variant="ghost" onClick={() => addSlot('video')}>
                  <Plus size={13} /> 视频
                </Button>
                <Button variant="ghost" onClick={() => addSlot('audio')}>
                  <Plus size={13} /> 音频
                </Button>
                <Button variant="ghost" onClick={() => addSlot('text')}>
                  <Plus size={13} /> 文本
                </Button>
              </>
            }
          >
            <div className="flex flex-col gap-2">
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
                      fixed ? 'border-ok-line bg-ok-soft/40' : 'border-line-soft bg-panel-soft',
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
                        <span className="flex h-4 w-4 items-center justify-center rounded-full bg-primary text-[10px] font-medium text-white">
                          {index + 1}
                        </span>
                      </span>
                      {slots.length > 1 && (
                        <button
                          type="button"
                          className="text-[11px] text-ink-mute hover:text-danger"
                          onClick={() =>
                            setDraft({
                              ...draft,
                              slots: slots.filter(item => item.key !== slot.key),
                            })
                          }
                        >
                          <Trash2 size={13} />
                        </button>
                      )}
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
                      <FormField label="来源">
                        <Select
                          block
                          value={slot.source ?? 'asset'}
                          options={SOURCE_OPTIONS}
                          onChange={event =>
                            patchSlot(index, {
                              source: event.target.value as SpecSlotIn['source'],
                            })
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
                    <div className="mt-2 grid gap-2 sm:grid-cols-[1fr_auto]">
                      <FormField label="进提示词的用途说明">
                        <TextInput
                          value={slot.usage ?? ''}
                          placeholder="例如：确定同款同色外观；只补纹理与扣件"
                          onChange={event => patchSlot(index, {usage: event.target.value})}
                        />
                      </FormField>
                      <div className="flex items-end gap-2">
                        {(slot.source ?? 'asset') === 'asset' &&
                          (slot.binding ?? 'user') === 'user' &&
                          slot.type !== 'text' && (
                            <>
                              <FormField label="最少">
                                <TextInput
                                  value={String(slot.minCount ?? 1)}
                                  onChange={event =>
                                    patchSlot(index, {minCount: Number(event.target.value || 0)})
                                  }
                                  className="w-16"
                                />
                              </FormField>
                              <FormField label="最多">
                                <TextInput
                                  value={String(slot.maxCount ?? 1)}
                                  onChange={event =>
                                    patchSlot(index, {maxCount: Number(event.target.value || 1)})
                                  }
                                  className="w-16"
                                />
                              </FormField>
                            </>
                          )}
                        {fixed && (
                          <Button variant="ghost" onClick={() => setPicker(index)}>
                            {(slot.source ?? 'asset') === 'pack' ? '选择封装' : '选择素材'}
                          </Button>
                        )}
                      </div>
                    </div>
                    {fixed && (
                      <p className="mt-1 truncate text-[11px] text-ink-mute">
                        {(slot.source ?? 'asset') === 'pack'
                          ? `封装：${slot.packId || '未选择'}`
                          : `素材：${slot.assetId || '未选择'}`}
                      </p>
                    )}
                    {(slot.refMode ?? 'mixed') === 'attachment' && (
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

        <div className="flex flex-col gap-4">
          <Section tone="warn" title="固定段" hint="模板库只读">
            <PromptBox
              value={draft.prompt.locked ?? ''}
              chips={chips}
              onRef={token => writeRef('locked', token)}
              onCaret={at => setCaret({field: 'locked', at})}
              onChange={text => setPromptText('locked', text)}
              readOnly
            />
          </Section>
          <Section
            tone="primary"
            title="可编辑段"
            hint="模板库可改"
            extra={
              <Button variant="ghost" onClick={() => addSlot('text')}>
                <Plus size={13} /> 文本素材
              </Button>
            }
          >
            <PromptBox
              value={draft.prompt.editable ?? ''}
              chips={chips}
              onRef={token => writeRef('editable', token)}
              onCaret={at => setCaret({field: 'editable', at})}
              onChange={text => setPromptText('editable', text)}
            />
            {hint && <p className="mt-2 text-[11px] text-warn-deep">{hint}</p>}
          </Section>
        </div>
      </div>

      <AssetPicker
        open={picker !== null}
        kind={
          picker === null
            ? 'image'
            : ((slots[picker].source === 'pack' ? 'pack' : slots[picker].type) as
                | 'image'
                | 'video'
                | 'audio'
                | 'text'
                | 'pack')
        }
        selected={picker === null ? [] : [slots[picker].assetId ?? ''].filter(Boolean)}
        onClose={() => setPicker(null)}
        onToggle={pick}
      />
    </section>
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
  tone: 'primary' | 'violet' | 'warn';
  title: string;
  hint?: string;
  extra?: ReactNode;
  children: ReactNode;
}) {
  const bar = {primary: 'bg-primary', violet: 'bg-violet', warn: 'bg-warn'}[tone];
  return (
    <div className="rounded-2xl border border-line bg-panel p-4 shadow-[0_1px_2px_rgba(16,42,55,0.04)]">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-xs font-semibold text-ink">
          <span className={cn('h-3 w-1 rounded-full', bar)} />
          {title}
          {hint && <span className="font-normal text-ink-mute">· {hint}</span>}
        </h3>
        {extra && <div className="flex items-center gap-1">{extra}</div>}
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
  readOnly = false,
}: {
  value: string;
  chips: {token: string; label: string; all: boolean}[];
  onRef: (token: string) => void;
  onCaret: (at: number) => void;
  onChange: (text: string) => void;
  readOnly?: boolean;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  return (
    <div>
      <textarea
        ref={ref}
        value={value}
        rows={7}
        readOnly={readOnly}
        placeholder={
          readOnly ? '这段由封装决定，模板库不可改' : '写描述文字；引用用下方按钮插入'
        }
        onChange={event => {
          onChange(event.target.value);
          onCaret(event.target.selectionStart ?? 0);
        }}
        onSelect={event => onCaret(event.currentTarget.selectionStart ?? 0)}
        className={cn(
          'w-full rounded-lg border px-2.5 py-2 text-xs leading-relaxed outline-none',
          readOnly
            ? 'cursor-default border-line-soft bg-canvas/60 text-ink-soft'
            : 'border-primary-line bg-primary-soft/30 text-ink focus:border-primary',
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

function SpecRow({
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
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line-soft px-4 py-3 last:border-b-0">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="truncate text-xs font-medium text-ink">{item.name}</span>
          {!trashed && (
            <Chip
              tone={
                item.status === 'published' ? 'ok' : item.status === 'disabled' ? 'warn' : 'default'
              }
            >
              {STATUS_LABELS[item.status] ?? item.status}
            </Chip>
          )}
          {!trashed && item.currentVersion > 0 && (
            <span className="text-[11px] text-ink-mute">v{item.currentVersion}</span>
          )}
        </div>
        <p className="mt-0.5 truncate text-[11px] text-ink-mute">
          {trashed
            ? `删除于 ${item.deletedAt ? new Date(item.deletedAt).toLocaleString() : '—'}`
            : `${slotCount} 个槽位`}
        </p>
      </div>
      <div className="flex items-center gap-1.5">
        {trashed ? (
          <Button variant="ghost" disabled={busy} onClick={onRestore}>
            <RotateCcw size={13} /> 恢复
          </Button>
        ) : (
          <>
            <Button variant="ghost" onClick={onEdit}>
              编辑
            </Button>
            {item.status !== 'disabled' && (
              <Button variant="ghost" disabled={busy} onClick={onPublish}>
                发布
              </Button>
            )}
            {item.status === 'published' && (
              <Button variant="ghost" disabled={busy} onClick={onDisable}>
                停用
              </Button>
            )}
            <Button variant="ghost" disabled={busy} onClick={onRemove}>
              删除
            </Button>
          </>
        )}
      </div>
    </div>
  );
}
