import { useState } from 'react';
import { Link } from 'react-router-dom';
import { GripVertical, Plus } from 'lucide-react';
import { useAvailablePacks } from '@/entities/template/api/pack.queries';
import { useManifestMutations, useManifests } from '@/entities/template-manifest/api/manifest.queries';
import { useCan } from '@/shared/access';
import type { ManifestOut, PublishedTemplateOut } from '@/shared/api/schema';
import { Button, Chip, EmptyState, FormField, Select, TextInput } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import { PACK_SCOPES, baseRole, roleLabel, rolesFor } from '@/entities/template/model/packRoles';
import { AssetPicker, type PickedAsset } from './AssetPicker';
import {
  KIND_LABELS,
  blankDraft,
  blankFunction,
  blankPack,
  bindingsFor,
  fromManifest,
  moveItem,
  toPayload,
  type AspectRatio,
  type Draft,
  type DraftPack,
  type PackKind,
} from './manifestDraft';

const KIND_OPTIONS = (Object.keys(KIND_LABELS) as PackKind[]).map(value => ({
  value,
  label: KIND_LABELS[value],
}));

const RATIO_OPTIONS: {value: AspectRatio; label: string}[] = [
  {value: '9:16', label: '9:16 竖屏'},
  {value: '16:9', label: '16:9 横屏'},
  {value: '1:1', label: '1:1 方形'},
];

const STATUS_LABELS: Record<string, string> = {
  draft: '草稿',
  published: '已发布',
  disabled: '已停用',
};

/** 拖拽中的参考图：包用 `key` 而不是下标，重排过程中下标会变。 */
type RefDrag = {packKey: string; index: number};

export function ManifestWorkspace() {
  const list = useManifests();
  const {validate, save, publish, disable} = useManifestMutations();
  const available = useAvailablePacks('all', '', 1);
  const canWrite = useCan('template_pack:create');
  const canPublish = useCan('template_pack:publish');
  const [draft, setDraft] = useState<Draft | null>(null);
  const [note, setNote] = useState('');
  const [error, setError] = useState('');
  const [published, setPublished] = useState<PublishedTemplateOut[]>([]);
  const [picker, setPicker] = useState<{kind: 'image' | 'video'; target: string} | null>(null);
  const [refDrag, setRefDrag] = useState<RefDrag | null>(null);
  const [fnDrag, setFnDrag] = useState<number | null>(null);

  const packs = available.data?.items ?? [];
  const busy = save.isPending || publish.isPending || disable.isPending || validate.isPending;

  const clearReport = () => {
    setError('');
    setNote('');
    setPublished([]);
  };

  const report = (run: () => Promise<unknown>, ok: string) => {
    clearReport();
    run()
      .then(() => setNote(ok))
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : String(reason)));
  };

  const submit = async (action: 'validate' | 'save' | 'publish') => {
    if (!draft) return;
    const payload = toPayload(draft);
    clearReport();
    try {
      if (action === 'validate') {
        const result = await validate.mutateAsync(payload);
        setNote(`预检通过 · 指纹 ${result.fingerprint.slice(0, 12)}`);
        return;
      }
      const saved = await save.mutateAsync(payload);
      if (action === 'save') {
        setNote(`已保存草稿（第 ${saved.revision} 次修订）`);
        setDraft(fromManifest(saved));
        return;
      }
      const receipt = await publish.mutateAsync(saved.key);
      setNote(`已发布 v${receipt.version}`);
      setPublished(receipt.templates ?? []);
      setDraft(fromManifest(receipt.manifest));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  const toggleAsset = (picked: PickedAsset) => {
    if (!draft || !picker) return;
    if (picker.target === 'video') {
      setDraft({...draft, videoAssetId: picked.id});
      return;
    }
    const index = Number(picker.target.split(':')[1]);
    const pack = draft.packs[index];
    const exists = pack.refs.some(ref => ref.assetId === picked.id);
    const refs = exists
      ? pack.refs.filter(ref => ref.assetId !== picked.id)
      : [...pack.refs, {assetId: picked.id, role: baseRole(pack.kind), description: ''}];
    updatePack(index, {refs});
  };

  const updatePack = (index: number, patch: Partial<DraftPack>) => {
    if (!draft) return;
    const next = draft.packs.slice();
    next[index] = {...next[index], ...patch};
    setDraft({...draft, packs: next});
  };

  const updateRef = (packIndex: number, refIndex: number, patch: {role?: string; description?: string}) => {
    if (!draft) return;
    const pack = draft.packs[packIndex];
    const refs = pack.refs.slice();
    refs[refIndex] = {...refs[refIndex], ...patch};
    updatePack(packIndex, {refs});
  };

  const pickerSelected = (() => {
    if (!draft || !picker) return [];
    if (picker.target === 'video') return draft.videoAssetId ? [draft.videoAssetId] : [];
    return draft.packs[Number(picker.target.split(':')[1])]?.refs.map(ref => ref.assetId) ?? [];
  })();

  /* ---------- 列表 ---------- */
  if (!draft) {
    return (
      <section className="flex flex-col gap-4">
        <div className="flex items-center justify-between">
          <p className="text-xs text-ink-mute">一份配置描述一支原片与它的若干功能，发布后成为模板库里的卡片。</p>
          {canWrite && (
            <Button onClick={() => setDraft(blankDraft())}>
              <Plus size={14} /> 新建封装
            </Button>
          )}
        </div>
        {error && <p className="text-xs text-danger">{error}</p>}
        {list.isLoading ? (
          <p className="py-10 text-center text-xs text-ink-mute">正在读取配置…</p>
        ) : (list.data?.items ?? []).length === 0 ? (
          <EmptyState title="还没有封装配置" description="新建一份配置，选择原片、素材包与提示词，发布后即可在工作台使用。" />
        ) : (
          <div className="overflow-hidden rounded-2xl border border-line bg-panel">
            {(list.data?.items ?? []).map(item => (
              <ManifestRow
                key={item.id}
                item={item}
                busy={busy}
                canPublish={canPublish}
                onEdit={() => {
                  setDraft(fromManifest(item));
                  clearReport();
                }}
                onPublish={() => report(() => publish.mutateAsync(item.key), `已发布 ${item.name}`)}
                onDisable={() => report(() => disable.mutateAsync(item.key), `已停用 ${item.name}`)}
              />
            ))}
          </div>
        )}
      </section>
    );
  }

  /* ---------- 编辑器 ---------- */
  return (
    <section className="flex flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <button
            type="button"
            className="text-xs text-ink-mute hover:text-ink"
            onClick={() => {
              setDraft(null);
              clearReport();
            }}
          >
            ← 返回列表
          </button>
          <Chip>{draft.name || '未命名封装'}</Chip>
        </div>
        {canWrite && (
          <div className="flex items-center gap-2">
            <Button variant="ghost" disabled={busy} onClick={() => void submit('validate')}>预检</Button>
            <Button variant="ghost" disabled={busy} onClick={() => void submit('save')}>保存草稿</Button>
            {canPublish && (
              <Button disabled={busy} onClick={() => void submit('publish')}>保存并发布</Button>
            )}
          </div>
        )}
      </div>

      {(note || error || published.length > 0) && (
        <div
          className={cn(
            'rounded-xl border px-3 py-2 text-[11px] leading-relaxed',
            error ? 'border-danger/40 bg-danger/5 text-danger' : 'border-line bg-panel-soft text-ink-soft',
          )}
        >
          <p className="whitespace-pre-wrap">{error || note}</p>
          {published.length > 0 && (
            <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1">
              {published.map(item => (
                <Link key={item.templateId} to={item.workbenchPath} className="text-primary hover:underline">
                  {item.name} →
                </Link>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[1.1fr_1fr]">
        <div className="flex flex-col gap-4">
          <div className="rounded-2xl border border-line bg-panel p-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <FormField label="配置标识" required hint="幂等边界：同 key 同内容重复保存不会新增">
                <TextInput
                  value={draft.key}
                  onChange={event => setDraft({...draft, key: event.target.value})}
                />
              </FormField>
              <FormField label="名称" required>
                <TextInput
                  value={draft.name}
                  onChange={event => setDraft({...draft, name: event.target.value})}
                />
              </FormField>
            </div>
            <div className="mt-3">
              <FormField label="原片" required hint={draft.videoAssetId || '未选择'}>
                <Button variant="ghost" onClick={() => setPicker({kind: 'video', target: 'video'})}>
                  选择原片
                </Button>
              </FormField>
            </div>
          </div>

          <div className="rounded-2xl border border-line bg-panel p-4">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-xs font-medium text-ink">素材包</h3>
              <Button variant="ghost" onClick={() => setDraft({...draft, packs: [...draft.packs, blankPack()]})}>
                <Plus size={13} /> 添加
              </Button>
            </div>
            <div className="flex flex-col gap-3">
              {draft.packs.map((pack, index) => (
                <div key={pack.key} className="rounded-xl border border-line-soft bg-panel-soft p-3">
                  <div className="grid gap-2 sm:grid-cols-3">
                    <FormField label="包 key">
                      <TextInput value={pack.key} onChange={event => updatePack(index, {key: event.target.value})} />
                    </FormField>
                    <FormField label="类型">
                      <Select
                        block
                        value={pack.kind}
                        options={KIND_OPTIONS}
                        onChange={event =>
                          updatePack(index, {
                            kind: event.target.value as PackKind,
                            refs: pack.refs.map(ref => ({
                              assetId: ref.assetId,
                              role: baseRole(event.target.value),
                              description: ref.description,
                            })),
                          })
                        }
                      />
                    </FormField>
                    <FormField label="来源">
                      <Select
                        block
                        value={pack.mode}
                        options={[
                          {value: 'new', label: '新建素材包'},
                          {value: 'reuse', label: '复用已发布包'},
                        ]}
                        onChange={event => updatePack(index, {mode: event.target.value as DraftPack['mode']})}
                      />
                    </FormField>
                  </div>

                  {pack.mode === 'reuse' ? (
                    <div className="mt-2">
                      <FormField label="已发布素材包">
                        <Select
                          block
                          value={pack.packId}
                          options={[
                            {value: '', label: '选择一个已发布的包'},
                            ...packs
                              .filter(item => item.kind === pack.kind)
                              .map(item => ({value: item.id, label: `${item.name}（v${item.version}）`})),
                          ]}
                          onChange={event => updatePack(index, {packId: event.target.value})}
                        />
                      </FormField>
                    </div>
                  ) : (
                    <>
                      <div className="mt-2 grid gap-2 sm:grid-cols-2">
                        <FormField label="包名称" required>
                          <TextInput value={pack.name} onChange={event => updatePack(index, {name: event.target.value})} />
                        </FormField>
                        <FormField label="可替换范围" hint="产品包必须至少选一个">
                          <div className="flex flex-wrap gap-1.5 pt-1">
                            {Object.entries(PACK_SCOPES).map(([value, label]) => {
                              const active = pack.scopes.includes(value);
                              return (
                                <button
                                  key={value}
                                  type="button"
                                  onClick={() =>
                                    updatePack(index, {
                                      scopes: active
                                        ? pack.scopes.filter(item => item !== value)
                                        : [...pack.scopes, value],
                                    })
                                  }
                                  className={cn(
                                    'rounded-full border px-2 py-0.5 text-[11px] transition-colors',
                                    active ? 'border-primary-line bg-primary/5 text-primary' : 'border-line text-ink-mute',
                                  )}
                                >
                                  {label}
                                </button>
                              );
                            })}
                          </div>
                        </FormField>
                      </div>
                      <div className="mt-2">
                        <div className="mb-1 flex items-center justify-between">
                          <span className="text-[11px] text-ink-mute">
                            参考图 {pack.refs.length} 张 · 拖动手柄调整编号
                          </span>
                          <Button variant="ghost" onClick={() => setPicker({kind: 'image', target: `pack:${index}`})}>
                            选择图片
                          </Button>
                        </div>
                        <div className="flex flex-col gap-1.5">
                          {pack.refs.map((ref, refIndex) => (
                            <div
                              key={ref.assetId}
                              onDragOver={event => {
                                if (refDrag?.packKey === pack.key) event.preventDefault();
                              }}
                              onDrop={event => {
                                if (refDrag?.packKey !== pack.key) return;
                                event.preventDefault();
                                const refs = moveItem(pack.refs, refDrag.index, refIndex);
                                setRefDrag(null);
                                if (refs !== pack.refs) updatePack(index, {refs});
                              }}
                              className={cn(
                                'rounded-lg border border-transparent px-1 py-1 transition-colors',
                                refDrag?.packKey === pack.key &&
                                  refDrag.index === refIndex &&
                                  'border-primary-line bg-primary/5',
                              )}
                            >
                              <div className="flex items-center gap-2">
                                <span
                                  draggable
                                  onDragStart={() => setRefDrag({packKey: pack.key, index: refIndex})}
                                  onDragEnd={() => setRefDrag(null)}
                                  className="cursor-grab text-ink-mute active:cursor-grabbing"
                                  title="拖动调整顺序"
                                >
                                  <GripVertical size={13} />
                                </span>
                                <span className="w-14 shrink-0 text-[11px] text-ink-mute">第 {refIndex + 1} 张</span>
                                <Select
                                  block
                                  value={ref.role}
                                  options={Object.entries(rolesFor(pack.kind)).map(([value, label]) => ({value, label}))}
                                  onChange={event => updateRef(index, refIndex, {role: event.target.value})}
                                />
                                <span className="shrink-0 text-[11px] text-ink-mute">
                                  {`{{Mixed ${refIndex + 2}}}`}
                                </span>
                              </div>
                              <div className="mt-1 pl-[22px]">
                                <TextInput
                                  value={ref.description}
                                  placeholder="这图看什么（可选）"
                                  onChange={event => updateRef(index, refIndex, {description: event.target.value})}
                                  className="w-full"
                                />
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    </>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="rounded-2xl border border-line bg-panel p-4">
          <div className="mb-3 flex items-center justify-between">
            <h3 className="text-xs font-medium text-ink">功能</h3>
            <Button variant="ghost" onClick={() => setDraft({...draft, functions: [...draft.functions, blankFunction()]})}>
              <Plus size={13} /> 添加
            </Button>
          </div>
          <div className="flex flex-col gap-3">
            {draft.functions.map((fn, index) => {
              const pack = draft.packs.find(item => item.key === fn.packKey);
              const scene = fn.scenePrompt.trim().length > 0;
              const update = (patch: Partial<typeof fn>) => {
                const next = draft.functions.slice();
                next[index] = {...fn, ...patch};
                setDraft({...draft, functions: next});
              };
              const move = (to: number) => {
                const functions = moveItem(draft.functions, index, to);
                if (functions !== draft.functions) setDraft({...draft, functions});
              };
              return (
                <div
                  key={fn.key}
                  onDragOver={event => {
                    if (fnDrag !== null) event.preventDefault();
                  }}
                  onDrop={event => {
                    if (fnDrag === null) return;
                    event.preventDefault();
                    move(fnDrag);
                    setFnDrag(null);
                  }}
                  className={cn(
                    'rounded-xl border border-line-soft bg-panel-soft p-3 transition-colors',
                    fnDrag === index && 'border-primary-line bg-primary/5',
                  )}
                >
                  <div className="mb-2 flex items-center justify-between">
                    <span
                      draggable
                      onDragStart={() => setFnDrag(index)}
                      onDragEnd={() => setFnDrag(null)}
                      className="flex cursor-grab items-center gap-1 text-[11px] text-ink-mute active:cursor-grabbing"
                      title="拖动调整顺序"
                    >
                      <GripVertical size={13} />
                      第 {index + 1} 个
                    </span>
                    {draft.functions.length > 1 && (
                      <button
                        type="button"
                        className="text-[11px] text-ink-mute hover:text-danger"
                        onClick={() =>
                          setDraft({...draft, functions: draft.functions.filter(item => item.key !== fn.key)})
                        }
                      >
                        移除
                      </button>
                    )}
                  </div>
                  <div className="grid gap-2 sm:grid-cols-2">
                    <FormField label="功能 key">
                      <TextInput value={fn.key} onChange={event => update({key: event.target.value})} />
                    </FormField>
                    <FormField label="功能名称" required>
                      <TextInput value={fn.name} onChange={event => update({name: event.target.value})} />
                    </FormField>
                  </div>
                  <div className="mt-2">
                    <FormField label="提示词" required hint="用 {{Mixed N}} 引用素材，第 1 位始终是原片">
                      <textarea
                        value={fn.prompt}
                        onChange={event => update({prompt: event.target.value})}
                        rows={5}
                        className="w-full rounded-lg border border-line bg-white px-2.5 py-2 text-xs leading-relaxed text-ink outline-none focus:border-primary-line"
                      />
                    </FormField>
                  </div>
                  <div className="mt-2 grid gap-2 sm:grid-cols-3">
                    <FormField label="使用素材包">
                      <Select
                        block
                        value={fn.packKey}
                        disabled={scene}
                        options={[
                          {value: '', label: '不选（文字换场景）'},
                          ...draft.packs.map(item => ({value: item.key, label: item.key})),
                        ]}
                        onChange={event => update({packKey: event.target.value})}
                      />
                    </FormField>
                    <FormField label="画幅">
                      <Select
                        block
                        value={fn.aspectRatio}
                        options={RATIO_OPTIONS}
                        onChange={event => update({aspectRatio: event.target.value as AspectRatio})}
                      />
                    </FormField>
                    <FormField label="时长（秒）" hint="留空用默认">
                      <TextInput
                        value={fn.duration}
                        inputMode="numeric"
                        onChange={event => update({duration: event.target.value.replace(/\D/g, '')})}
                      />
                    </FormField>
                  </div>

                  {scene ? (
                    <div className="mt-2">
                      <FormField label="场景描述（默认值）">
                        <textarea
                          value={fn.scenePrompt}
                          onChange={event => update({scenePrompt: event.target.value})}
                          rows={3}
                          className="w-full rounded-lg border border-line bg-white px-2.5 py-2 text-xs leading-relaxed text-ink outline-none focus:border-primary-line"
                        />
                      </FormField>
                    </div>
                  ) : (
                    <div className="mt-2 flex items-center justify-between gap-2">
                      <label className="flex items-center gap-1.5 text-[11px] text-ink-soft">
                        <input
                          type="checkbox"
                          checked={fn.useRoles}
                          onChange={event => update({useRoles: event.target.checked})}
                        />
                        按用途绑定编号
                      </label>
                      {pack && fn.useRoles && (
                        <span className="truncate text-[11px] text-ink-mute">
                          {Object.entries(bindingsFor(pack))
                            .map(([token, role]) =>
                              `${token} → ${role === 'source_video' ? '原片' : roleLabel(pack.kind, role)}`,
                            )
                            .join(' · ')}
                        </span>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </div>

      <AssetPicker
        open={picker !== null}
        kind={picker?.kind ?? 'image'}
        selected={pickerSelected}
        onClose={() => setPicker(null)}
        onToggle={toggleAsset}
      />
    </section>
  );
}

function ManifestRow({
  item,
  busy,
  canPublish,
  onEdit,
  onPublish,
  onDisable,
}: {
  item: ManifestOut;
  busy: boolean;
  canPublish: boolean;
  onEdit: () => void;
  onPublish: () => void;
  onDisable: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line-soft px-4 py-3 last:border-b-0">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="truncate text-xs font-medium text-ink">{item.name}</span>
          <Chip tone={item.status === 'published' ? 'ok' : item.status === 'disabled' ? 'warn' : 'default'}>
            {STATUS_LABELS[item.status] ?? item.status}
          </Chip>
          {item.version > 0 && <span className="text-[11px] text-ink-mute">v{item.version}</span>}
        </div>
        <p className="mt-0.5 truncate text-[11px] text-ink-mute">
          {item.key} · {item.functions?.length ?? 0} 个功能
        </p>
      </div>
      <div className="flex items-center gap-1.5">
        <Button variant="ghost" onClick={onEdit}>编辑</Button>
        {canPublish && item.status === 'draft' && (
          <Button variant="ghost" disabled={busy} onClick={onPublish}>发布</Button>
        )}
        {canPublish && item.status === 'published' && (
          <Button variant="ghost" disabled={busy} onClick={onDisable}>停用</Button>
        )}
      </div>
    </div>
  );
}
