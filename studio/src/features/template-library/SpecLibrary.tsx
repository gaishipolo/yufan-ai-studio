import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { ArrowRight, Play, Plus } from 'lucide-react';
import { KIND_LABEL } from '@/entities/library/model/types';
import { AssetChip } from '@/entities/library/ui/AssetChip';
import { CaseCover } from '@/entities/library/ui/CaseCover';
import { PackChip } from '@/entities/library/ui/PackChip';
import { PromptRefs } from '@/entities/library/ui/PromptRefs';
import { SlotMediaPicker } from './SlotMediaPicker';
import { useGenerateImage } from '@/entities/image/api/image.api';
import { useModels } from '@/entities/model/api/model.queries';
import { paramControlsFor, resolveParamValues, type ParamValue } from '@/entities/model/lib/paramForm';
import { useCreateTask } from '@/entities/task/api/task.queries';
import { usePublishedSpecs, useSpecCompile } from '@/entities/template-spec/api/spec.queries';
import type { SpecSlotOut, TemplateSpecOut } from '@/shared/api/schema';
import { AssetPicker, type PickedAsset } from '@/features/template-specs/AssetPicker';
import { Button, Chip, EmptyState, SearchInput, Skeleton } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import { toSubmitPayload, type SubmitKind } from './taskPayload';

/**
 * 模板库（填表）：左边选模板，右边就地填素材 → 生成。
 *
 * 与「模板规格」的分工：那边**定表**（槽位、提示词、模型），这边**填表**（挑素材、
 * 可选地改写可编辑段），提交后走 `compile` → `POST /tasks`。所以本页不出现任何
 * 模板编辑控件 —— 要改模板回规格页；这里只有「填」和「生成」两种动作。
 *
 * 页面不做规则校验：数量上下限、素材可读性、编号展开都在服务端。
 * 这里只把「还缺哪几个槽位」算出来禁用按钮 —— 那是提交前置条件，不是业务规则。
 */

/* 类型名与卡片上的类型标签同源：都是资产库的 kind。别再写一份本地表 ——
   漏掉 `pack` 时页面上会直接印出英文 `pack`（真机截到过）。 */
const TYPE_LABEL: Record<string, string> = KIND_LABEL;

function slotsOf(spec: TemplateSpecOut | undefined): SpecSlotOut[] {
  return spec?.latest?.slots ?? [];
}

/** 封装槽位的条数由成员数决定（1/1 是占位），所以上下限一律按 1 算。 */
function minOf(slot: SpecSlotOut): number {
  return slot.type === 'pack' ? 1 : (slot.minCount ?? 1);
}

function maxOf(slot: SpecSlotOut): number {
  return slot.type === 'pack' ? 1 : (slot.maxCount ?? 1);
}

/**
 * 展示案例：作者上传/选的样例图或视频。
 *
 * 没设就明写「暂无案例」，不摆渐变块冒充缩略图 —— 那会让人以为模板带图。
 * 它是**纯展示**字段（存在草稿里、不进发布快照），所以这里只读、不可交互。
 */
/**
 * 模板卡片：案例封面 + 压在图上的名称 + 「要什么素材」的标签 + 一行模型。
 *
 * 名称压在封面底部而不是另起一行：卡片是「挑图」，图应该占满视觉重心，文字只做
 * 标注。版本号反而放右上角 —— 它是作者关心的东西，不该占掉选模板时的注意力。
 */
function TemplateCard({
  item,
  selected,
  onSelect,
}: {
  item: TemplateSpecOut;
  selected: boolean;
  onSelect: () => void;
}) {
  const itemSlots = slotsOf(item);
  const fixed = itemSlots.filter(slot => slot.binding === 'fixed').length;
  const pending = itemSlots.length - fixed;
  // 要什么类型的素材：卡片上一眼能看出来，省得点进去才发现是视频模板。
  const kinds = [...new Set(itemSlots.map(slot => slot.type))];
  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn(
        'group flex flex-col rounded-2xl border bg-panel p-1.5 text-left transition-all duration-200',
        selected
          ? 'border-primary-line shadow-ring'
          : 'border-line-soft hover:-translate-y-0.5 hover:border-line hover:shadow-card',
      )}
    >
      <div className="relative overflow-hidden rounded-xl">
        <CaseCover
          assetId={item.demoAssetId}
          seed={item.key}
          className="aspect-[4/5] w-full"
          hoverPlay
        />
        <span className="pointer-events-none absolute right-1.5 top-1.5 rounded-full bg-black/40 px-1.5 py-0.5 text-[10px] font-medium leading-[14px] text-white/95 backdrop-blur-sm">
          v{item.currentVersion}
        </span>
        <div className="pointer-events-none absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/70 via-black/25 to-transparent px-2.5 pb-1.5 pt-9">
          <div className="truncate text-[12.5px] font-medium text-white">{item.name}</div>
        </div>
      </div>

      <div className="flex flex-col gap-1.5 px-1 pb-0.5 pt-2">
        <div className="flex flex-wrap gap-1">
          {itemSlots.length === 0 ? (
            <Chip tone="ok">纯提示词</Chip>
          ) : pending > 0 ? (
            <Chip tone="warn">待填 {pending}</Chip>
          ) : (
            <Chip tone="ok">素材已固定</Chip>
          )}
          {kinds.map(kind => (
            <Chip key={kind} tone="primary">
              {TYPE_LABEL[kind] ?? kind}
            </Chip>
          ))}
        </div>
        <div className="mt-0.5 flex items-center justify-between gap-2 border-t border-line-soft pt-1.5">
          <span className="min-w-0 truncate text-[10.5px] text-ink-mute">
            {item.latest?.model ?? '—'}
          </span>
          <span
            className={cn(
              'flex shrink-0 items-center gap-0.5 text-[11px] transition-colors',
              selected ? 'text-primary' : 'text-ink-mute group-hover:text-primary',
            )}
          >
            填写 <ArrowRight size={11} />
          </span>
        </div>
      </div>
    </button>
  );
}

/** 右栏分区：标题一行 + 内容；分区之间用细线，不再套一层边框盒。 */
function PanelSection({
  title,
  extra,
  children,
}: {
  title: string;
  extra?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="flex flex-col gap-2 border-t border-line-soft pt-3 first:border-t-0 first:pt-0">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-[11.5px] font-semibold tracking-[.2px] text-ink">{title}</h3>
        {extra}
      </div>
      {children}
    </section>
  );
}

export function SpecLibrary() {
  const published = usePublishedSpecs();
  const compile = useSpecCompile();
  const createTask = useCreateTask();
  const generateImage = useGenerateImage();
  const videoModels = useModels('video');
  const imageModels = useModels('image');

  const [keyword, setKeyword] = useState('');
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [values, setValues] = useState<Record<string, string[]>>({});
  const [picker, setPicker] = useState<string | null>(null);
  const [note, setNote] = useState('');
  const [error, setError] = useState('');

  const items = useMemo(() => {
    const all = published.data?.items ?? [];
    const word = keyword.trim().toLowerCase();
    return word ? all.filter(item => item.name.toLowerCase().includes(word)) : all;
  }, [published.data, keyword]);

  const spec = items.find(item => item.key === selectedKey) ?? items[0];
  const slots = slotsOf(spec);
  const version = spec?.latest;
  const promptText = version?.prompt ?? '';
  const focused = spec?.key ?? null;

  /* 换模板就清空上一次的填写：不做"记忆上次填的素材"，
     那会让用户以为新模板也用了同一张图。 */
  useEffect(() => {
    setValues({});
    setNote('');
    setError('');
  }, [focused, version?.version]);

  /* 模板只声明模型，「提交到哪个接口」由模型能力决定：视频进 /tasks、图片进
     /generations/image。判不出来（模型清单里没有）就按视频处理 —— 那是这条链路
     原本唯一支持的方向，报错信息也比「静默当图片」清楚。 */
  const imageModel = imageModels.find(item => item.id === version?.model);
  const videoModel = videoModels.find(item => item.id === version?.model);
  const model = imageModel ?? videoModel;
  const kind: SubmitKind = imageModel ? 'image' : 'video';
  const defaults =
    kind === 'image'
      ? {
          size: String(imageModel?.paramSchema?.size?.default ?? '') || undefined,
          count: Number(imageModel?.paramSchema?.count?.default ?? 0) || undefined,
        }
      : {
          durationSec: Number(videoModel?.paramSchema?.durationSec?.default ?? 0) || undefined,
          aspectRatio: (videoModel?.paramSchema?.aspectRatio?.default as string | undefined) ?? undefined,
        };

  const missing = slots.filter(slot => {
    if (slot.binding !== 'user') return false;
    return (values[slot.key] ?? []).length < minOf(slot);
  });

  /* 参数只读展示，标签与档位文案全部来自模型的 `paramSchema`（`paramControlsFor`）：
     模型多声明一个参数，这里就多一枚 chip，前端不写死任何一种、也不印英文 key。 */
  const paramControls = paramControlsFor(model);
  const overrides: Record<string, ParamValue> = {};
  for (const [key, value] of Object.entries(version?.params ?? {})) {
    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
      overrides[key] = value;
    }
  }
  const paramValues = resolveParamValues(paramControls, overrides);
  const paramChips = paramControls.map(control => {
    const raw = paramValues[control.key];
    const option = control.options.find(item => item.value === String(raw));
    const text =
      option?.label ??
      (raw === '' || raw === undefined ? '默认' : String(raw).replace(/(\d)x(\d)/, '$1×$2'));
    return {key: control.key, label: control.label, text};
  });

  /* 填了几项：固定槽位天然算填好，剩下的看用户选了没。进度条用它，省得只在
     按钮上写一句「还缺 2 项」——那句话得滚到底才看得见。 */
  const filledCount = slots.length - missing.length;
  const progress = slots.length > 0 ? Math.round((filledCount / slots.length) * 100) : 0;

  const busy = compile.isPending || createTask.isPending || generateImage.isPending;

  const pickerSlot = slots.find(slot => slot.key === picker);

  /* 选择器的语义是 `onToggle`（点已选的等于取消），多选槽位不关弹层：
     一次挑三张比反复开关三次省事；单选槽位选一张就替换掉原来的。 */
  const togglePick = (asset: PickedAsset) => {
    if (!pickerSlot) return;
    const key = pickerSlot.key;
    const limit = maxOf(pickerSlot);
    setValues(current => {
      const existing = current[key] ?? [];
      if (existing.includes(asset.id)) {
        return {...current, [key]: existing.filter(id => id !== asset.id)};
      }
      const next = limit > 1 ? [...existing, asset.id] : [asset.id];
      return {...current, [key]: next.slice(0, limit)};
    });
    /* 单选（含封装）选完就关：留着一个已经选好的弹层，用户只能再去点右上角，
       而这时提示词里的缩略图早就该出来了。多选槽位仍然不关，一次挑三张。 */
    if (limit <= 1) setPicker(null);
  };

  const submit = async () => {
    if (!spec) return;
    setError('');
    setNote('');
    try {
      const compiled = await compile.mutateAsync({
        key: spec.key,
        payload: {slotValues: values},
      });
      const plan = toSubmitPayload(compiled, spec.name, kind, defaults);
      if (!plan.ok) {
        setError(plan.message);
        return;
      }
      if (plan.kind === 'image') {
        const job = await generateImage.mutateAsync(plan.payload);
        // 生图的 `name` 是从提示词派生的，会显示成 `{{Text 1}}`；用模板名更好认
        setNote(`已提交生图：${spec.name}（${job.id}）`);
      } else {
        const task = await createTask.mutateAsync(plan.payload);
        setNote(`已提交：${task.title || task.id}`);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  if (published.isLoading) {
    return (
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {Array.from({length: 6}).map((_, index) => (
          <Skeleton key={index} className="h-[360px] rounded-2xl" />
        ))}
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <EmptyState
        title="还没有已发布的模板"
        description="先在「模板封装」里定一张表并发布，它就会出现在这里供填表使用。"
      />
    );
  }

  return (
    <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_400px]">
      <div className="flex flex-col gap-3">
        {/* 工具栏吸顶：模板多到要滚的时候，筛选框不该跟着滚走。
            偏移 54px 是顶栏高度 —— 那条 `sticky top-0` 的 header 在最上层，
            不躲开就会被盖住（第一次就是被它盖成一团糊影）。 */}
        <div className="sticky top-[54px] z-10 -mx-1 flex items-center gap-2.5 border-b border-line-soft bg-panel px-1 py-2">
          <SearchInput
            value={keyword}
            onChange={event => setKeyword(event.target.value)}
            placeholder="按名称筛选"
            wrapperClassName="w-56"
          />
          <span className="text-[11px] text-ink-mute">{items.length} 个模板</span>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {items.map(item => (
            <TemplateCard
              key={item.key}
              item={item}
              selected={item.key === spec?.key}
              onSelect={() => setSelectedKey(item.key)}
            />
          ))}
        </div>
      </div>

      <aside className="flex flex-col overflow-hidden rounded-2xl border border-line bg-panel shadow-[0_1px_2px_rgba(16,42,55,.05)] xl:sticky xl:top-[66px]">
        {spec && version ? (
          <>
            <header className="border-b border-line-soft bg-gradient-to-b from-panel-soft to-panel px-4 pb-3 pt-3.5">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="truncate text-[15px] font-semibold leading-tight text-ink">
                    {spec.name}
                  </div>
                  <div className="mt-1 flex min-w-0 items-center gap-1.5 text-[11px] text-ink-mute">
                    <span className="shrink-0 rounded-[5px] bg-panel px-1 py-px text-[10px] ring-1 ring-inset ring-line-soft">
                      v{version.version}
                    </span>
                    <span className="truncate">{version.model}</span>
                  </div>
                </div>
                <Link
                  to="/templates/specs"
                  className="shrink-0 rounded-md border border-line px-1.5 py-0.5 text-[11px] text-ink-soft transition-colors hover:border-primary-line hover:text-primary"
                >
                  改模板
                </Link>
              </div>
            </header>

            <div className="flex flex-col gap-3.5 px-4 py-3.5">
              {slots.length > 0 && (
                <PanelSection
                  title="填入素材"
                  extra={
                    /* 进度放在它衡量的东西旁边：放在标题栏里会变成一条压在名字下的
                       粗线，看着像分隔符，而不是"填了几项"。 */
                    <span className="flex items-center gap-1.5">
                      <span className="h-1 w-16 overflow-hidden rounded-full bg-line-soft">
                        <span
                          className={cn(
                            'block h-full rounded-full transition-all duration-300 ease-smooth',
                            filledCount === slots.length ? 'bg-ok' : 'bg-primary',
                          )}
                          style={{width: `${progress}%`}}
                        />
                      </span>
                      <span className="text-[10px] tabular-nums text-ink-mute">
                        {filledCount}/{slots.length}
                      </span>
                    </span>
                  }
                >
                  <div className="flex flex-col gap-2">
                    {slots.map((slot, index) => {
                      const filled = values[slot.key] ?? [];
                      const fixed = slot.binding === 'fixed';
                      const locked = fixed && (slot.type === 'pack' ? !!slot.packId : !!slot.assetId);
                      /* 要让用户填的图片/视频：按「一级标签 · 槽位名」分组、点二级标签看素材。
                         其余（固定槽位、文本/音频/封装）保留原来的行式写法。 */
                      if (!fixed && (slot.type === 'image' || slot.type === 'video')) {
                        return (
                          <SlotMediaPicker
                            key={slot.key}
                            slot={slot}
                            selected={filled}
                            onChange={ids =>
                              setValues(current => ({...current, [slot.key]: ids}))
                            }
                          />
                        );
                      }
                      return (
                        <div key={slot.key} className="flex gap-2">
                          <div className="flex w-24 shrink-0 flex-col gap-0.5">
                            <span className="flex items-baseline gap-1 text-[12px] leading-tight text-ink">
                              <span className="shrink-0 text-ink-mute">{index + 1}.</span>
                              <span className="line-clamp-2">{slot.label || slot.key}</span>
                              {!fixed && minOf(slot) > 0 && (
                                <span className="text-warn-deep">*</span>
                              )}
                            </span>
                            <span className="text-[10px] text-ink-mute">
                              {TYPE_LABEL[slot.type] ?? slot.type}
                              {fixed
                                ? ' · 已固定'
                                : maxOf(slot) > 1
                                  ? ` · 最多 ${maxOf(slot)}`
                                  : ''}
                            </span>
                          </div>

                          <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5">
                            {fixed && slot.type === 'pack' && slot.packId && (
                              <PackChip packId={slot.packId} />
                            )}
                            {fixed && slot.type !== 'pack' && slot.assetId && (
                              <AssetChip assetId={slot.assetId} />
                            )}
                            {!fixed && slot.type === 'pack' && filled[0] && (
                              <PackChip
                                packId={filled[0]}
                                onRemove={() => setValues(current => ({...current, [slot.key]: []}))}
                              />
                            )}
                            {!fixed &&
                              slot.type !== 'pack' &&
                              filled.map(assetId => (
                                <AssetChip
                                  key={assetId}
                                  assetId={assetId}
                                  onRemove={() =>
                                    setValues(current => ({
                                      ...current,
                                      [slot.key]: (current[slot.key] ?? []).filter(
                                        id => id !== assetId,
                                      ),
                                    }))
                                  }
                                />
                              ))}
                            {!fixed && !locked && (
                              <button
                                type="button"
                                onClick={() => setPicker(slot.key)}
                                className="flex items-center gap-1 rounded-lg border border-line-soft bg-panel-soft px-2 py-1 text-[11px] text-ink-soft transition-colors hover:border-primary-line hover:bg-primary-soft hover:text-primary"
                              >
                                <Plus size={11} />
                                {filled.length > 0
                                  ? '再选'
                                  : `选择${slot.type === 'pack' ? '封装' : (TYPE_LABEL[slot.type] ?? '素材')}`}
                              </button>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                  {/* 用途说明只在该槽位还没填时出现：填完它就是噪音。 */}
                  {slots.some(slot => slot.usage && (values[slot.key] ?? []).length === 0) && (
                    <ul className="mt-1 flex flex-col gap-0.5 border-t border-line-soft pt-2 text-[10px] leading-relaxed text-ink-mute">
                      {slots
                        .filter(slot => slot.usage && (values[slot.key] ?? []).length === 0)
                        .map(slot => (
                          <li key={slot.key} className="truncate">
                            {slot.label || slot.key}：{slot.usage}
                          </li>
                        ))}
                    </ul>
                  )}
                </PanelSection>
              )}

              {/* 提示词：占位符就地换成用户选的东西 —— 文本显示正文，图 / 视频显示
                  跟文字同高的缩略块；还没选的位置给一个「选择」按钮，点开就是选择器。 */}
              {promptText.trim() && (
                <PanelSection
                  title="提示词"
                  extra={<span className="text-[10px] text-ink-mute">不可编辑</span>}
                >
                  <PromptRefs
                    value={promptText}
                    className="block max-h-52 overflow-y-auto rounded-lg bg-panel-soft px-2.5 py-2 text-[11px] leading-relaxed text-ink-soft"
                    slot={(key, mode) => {
                      const target = slots.find(item => item.key === key);
                      if (!target) return null;
                      const fixed = target.binding === 'fixed';
                      if (target.type === 'pack') {
                        // 封装是「一组成员」：选定之后摊开前两张 + 「N 张」，
                        // 点开看全部。没选还是给「选择封装」按钮。
                        const packId = fixed
                          ? target.packId
                          : (values[key] ?? [])[0];
                        if (packId) return {kind: 'pack', packId};
                        return {
                          kind: 'empty',
                          label: '选择封装',
                          onPick: () => setPicker(key),
                        };
                      }
                      const picked = fixed
                        ? [target.assetId].filter(Boolean) as string[]
                        : (values[key] ?? []);
                      if (picked.length === 0) {
                        return {
                          kind: 'empty',
                          label: `选择${TYPE_LABEL[target.type] ?? '素材'}`,
                          onPick: () => setPicker(key),
                        };
                      }
                      const ids =
                        mode.kind === 'all'
                          ? picked
                          : mode.kind === 'index'
                            ? picked.slice(mode.at - 1, mode.at)
                            : picked.slice(0, 1);
                      if (ids.length === 0) {
                        return {kind: 'empty', label: '未选到这一份'};
                      }
                      return {kind: target.type === 'text' ? 'text' : 'asset', ids};
                    }}
                  />
                </PanelSection>
              )}

              {paramChips.length > 0 && (
                <PanelSection title="参数">
                  <div className="flex flex-wrap gap-1.5">
                    {paramChips.map(item => (
                      <Chip key={item.key}>
                        <span className="text-ink-mute">{item.label}</span>
                        <span className="ml-1 text-ink-soft">{item.text}</span>
                      </Chip>
                    ))}
                  </div>
                </PanelSection>
              )}
            </div>

            <div className="flex flex-col gap-2 border-t border-line-soft bg-panel-soft/60 px-4 py-3">
              {error && <p className="text-[11px] text-danger">{error}</p>}
              {note && (
                <p className="flex items-center gap-2 text-[11px] text-ink-soft">
                  <span className="truncate">{note}</span>
                  <Link to="/tasks" className="shrink-0 text-primary hover:underline">
                    去任务中心
                  </Link>
                </p>
              )}
              <Button
                block
                size="lg"
                disabled={busy || missing.length > 0}
                onClick={() => void submit()}
              >
                <Play size={13} />
                {busy
                  ? '提交中…'
                  : missing.length > 0
                    ? `还缺 ${missing.length} 项素材`
                    : kind === 'image'
                      ? '生成图片'
                      : '生成视频'}
              </Button>
            </div>
          </>
        ) : (
          <p className="px-4 py-8 text-center text-[12px] text-ink-mute">左侧选一个模板。</p>
        )}
      </aside>

      <AssetPicker
        open={picker !== null}
        kind={pickerSlot?.type ?? 'image'}
        /* 槽位的一级标签决定候选集：这里与封装页的固定选择器看的是同一批素材。 */
        tagIds={pickerSlot?.tagId ? [pickerSlot.tagId] : []}
        selected={pickerSlot ? (values[pickerSlot.key] ?? []) : []}
        onClose={() => setPicker(null)}
        onToggle={togglePick}
      />
    </div>
  );
}
