import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ArrowRight, ImageOff, Play, X } from 'lucide-react';
import { libraryApi } from '@/entities/library/api/library.api';
import { KIND_LABEL } from '@/entities/library/model/types';
import { AssetPreview } from '@/entities/library/ui/AssetPreview';
import { PromptRefs } from '@/entities/library/ui/PromptRefs';
import { AssetChip } from './AssetChip';
import { SlotMediaPicker } from './SlotMediaPicker';
import { useGenerateImage } from '@/entities/image/api/image.api';
import { useModels } from '@/entities/model/api/model.queries';
import { useCreateTask } from '@/entities/task/api/task.queries';
import { usePublishedSpecs, useSpecCompile } from '@/entities/template-spec/api/spec.queries';
import type { SpecSlotOut, TemplateSpecOut } from '@/shared/api/schema';
import { AssetPicker, type PickedAsset } from '@/features/template-specs/AssetPicker';
import { Button, Chip, CoverImage, EmptyState, SearchInput, Skeleton } from '@/shared/ui';
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

/** 参数的展示名：图片看尺寸/张数，视频看画幅/时长，其余按 key 原样。 */
const PARAM_LABEL: Record<string, string> = {
  size: '尺寸',
  count: '张数',
  durationSec: '时长',
  aspectRatio: '画幅',
  stylePreset: '风格',
};

/** 参数值的可读文案。没有值时明写「默认」，不摆一个空白 chip 让人猜。 */
function formatParam(key: string, value: unknown): string {
  const text = value === null || value === undefined || value === '' ? '' : String(value);
  if (!text) return `${PARAM_LABEL[key] ?? key} · 默认`;
  if (key === 'durationSec') return `${text} 秒`;
  if (key === 'count') return `${text} 张`;
  // 素材库里尺寸都写成 `1024x1536`，展示时换成乘号更像尺寸而不是型号
  return key === 'size' ? text.replace('x', '×') : text;
}

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
function DemoCase({
  assetId,
  className,
  seed = '',
}: {
  assetId: string | null;
  className?: string;
  /** 没案例时兜底渐变的种子，传模板 key：同一张模板每次都同一个色，不会闪。 */
  seed?: string;
}) {
  if (!assetId) {
    /* 空封面用资产卡的兜底渐变，不摆虚线框：5:6 的位置上那圈虚线比图还显眼，
       还容易被读成"加载失败"。渐变按 key 轮换，一排里不会全是同一块灰。 */
    return (
      <div className={cn('relative overflow-hidden', className)}>
        <CoverImage seed={seed || 'template'} className="h-full w-full" />
        <span className="absolute inset-0 flex flex-col items-center justify-center gap-1 text-white/75">
          <ImageOff size={16} />
          <span className="text-[10px]">暂无案例</span>
        </span>
      </div>
    );
  }
  /* 卡片：图片给封面，视频给封面 + 悬停自动播（静音循环）。取流只在 hover 时发生，
     一排卡片不会把每条视频都拉一遍。 */
  return <AssetPreview assetId={assetId} className={className} hoverPlay />;
}

/**
 * 模板卡片：竖版封面 + 名称/版本 + 「要什么素材」两行 + 底部动作。
 *
 * 封面按 4:5 给：模板案例多是竖屏成片，横着裁会把主体裁掉；文字部分只留这两行，
 * 再多就得进右栏看 —— 卡片是选，不是读。
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
        'group flex flex-col overflow-hidden rounded-xl border bg-panel text-left transition-all',
        selected
          ? 'border-primary-line shadow-[0_0_0_3px_rgba(43,124,255,0.10)]'
          : 'border-line hover:border-line-strong hover:shadow-card',
      )}
    >
      <div className="relative">
        <DemoCase
          assetId={item.demoAssetId ?? null}
          seed={item.key}
          className="aspect-[5/6] w-full"
        />
        {item.demoAssetId && (
          <span className="pointer-events-none absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/60 via-black/20 to-transparent px-2 pb-1.5 pt-8 text-[10px] text-white/90 transition-opacity group-hover:opacity-0">
            {selected ? '正在填写' : '点击填写素材'}
          </span>
        )}
      </div>
      <div className="flex flex-1 flex-col gap-1.5 p-2.5">
        <div className="flex items-center gap-1.5">
          <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-ink">
            {item.name}
          </span>
          <Chip>v{item.currentVersion}</Chip>
        </div>
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
          {fixed > 0 && pending > 0 && <Chip>{fixed} 固定</Chip>}
        </div>
        <dl className="mt-0.5 flex flex-col gap-0.5 text-[11px] leading-relaxed">
          <div className="flex gap-2">
            <dt className="w-8 shrink-0 text-ink-mute">模型</dt>
            <dd className="min-w-0 flex-1 truncate text-ink-soft">
              {item.latest?.model ?? '—'}
            </dd>
          </div>
          <div className="flex gap-2">
            <dt className="w-8 shrink-0 text-ink-mute">素材</dt>
            <dd className="min-w-0 flex-1 truncate text-ink-soft">
              {itemSlots.length > 0
                ? `${itemSlots.length} 个槽位${fixed > 0 ? ` · ${fixed} 个已固定` : ''}`
                : '—'}
            </dd>
          </div>
        </dl>
        <div className="mt-auto flex items-center justify-between border-t border-line-soft pt-1.5 text-[11px]">
          <span className="text-ink-mute">
            {pending > 0 ? `需填 ${pending} 项` : '无需填素材'}
          </span>
          <span
            className={cn(
              'flex items-center gap-0.5 transition-colors',
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
    <section className="flex flex-col gap-1.5 border-t border-line-soft pt-3 first:border-t-0 first:pt-0">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-[11px] font-medium text-ink-soft">{title}</h3>
        {extra}
      </div>
      {children}
    </section>
  );
}

/** 资产库封装：成员数由库给，这里只显示「N 张」与首图，不展开成员顺序。 */
function PackSummary({packId, onRemove}: {packId: string; onRemove?: () => void}) {
  const pack = useQuery({
    queryKey: ['library', 'pack', packId],
    queryFn: () => libraryApi.packDetail(packId),
    enabled: !!packId,
    staleTime: 30_000,
  });
  const items = pack.data?.packItems ?? [];
  return (
    <span className="flex items-center gap-1.5 rounded-lg border border-line-soft bg-panel px-1.5 py-1">
      <CoverImage
        src={items[0]?.coverUrl ?? undefined}
        seed={packId}
        className="h-7 w-7 shrink-0 rounded"
      />
      <span className="max-w-[180px] truncate text-[11px] text-ink-soft">
        {pack.data?.name ?? packId.slice(0, 8)}
      </span>
      <span className="text-[11px] text-ink-mute">{items.length} 张</span>
      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          className="text-ink-mute transition-colors hover:text-danger"
          title="移除"
        >
          <X size={11} />
        </button>
      )}
    </span>
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

  /* 参数只读展示，键与顺序都来自模型的 `paramSchema`：图片模型是尺寸/张数，
     视频模型是画幅/时长，前端不写死任何一种。模型不在目录里就整块不渲染。 */
  const paramChips = Object.entries(model?.paramSchema ?? {}).map(([key, field]) => {
    const value = version?.params?.[key] ?? field?.default;
    return {key, text: formatParam(key, value)};
  });

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
      <div className="grid gap-3 sm:grid-cols-2 2xl:grid-cols-3">
        {Array.from({length: 6}).map((_, index) => (
          <Skeleton key={index} className="h-[320px] rounded-xl" />
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
    <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_392px]">
      <div className="flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <SearchInput
            value={keyword}
            onChange={event => setKeyword(event.target.value)}
            placeholder="按名称筛选"
            wrapperClassName="w-52 bg-panel"
          />
          <span className="text-[11px] text-ink-mute">{items.length} 个模板</span>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 2xl:grid-cols-3">
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

      <aside className="flex flex-col overflow-hidden rounded-xl border border-line bg-panel xl:sticky xl:top-4">
        {spec && version ? (
          <>
            <header className="flex items-start justify-between gap-2 border-b border-line-soft px-4 py-3">
              <div className="min-w-0">
                <div className="truncate text-[14px] font-medium text-ink">{spec.name}</div>
                <div className="mt-0.5 truncate text-[11px] text-ink-mute">
                  v{version.version} · {version.model}
                </div>
              </div>
              <Link
                to="/templates/specs"
                className="shrink-0 text-[11px] text-ink-mute transition-colors hover:text-primary"
              >
                改模板
              </Link>
            </header>

            <div className="flex flex-col gap-3 px-4 py-3">
              {slots.length > 0 && (
                <PanelSection
                  title="填入素材"
                  extra={
                    missing.length > 0 ? (
                      <span className="text-[11px] text-warn-deep">还缺 {missing.length} 项</span>
                    ) : (
                      <span className="text-[11px] text-ok-deep">已填满</span>
                    )
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
                              <PackSummary packId={slot.packId} />
                            )}
                            {fixed && slot.type !== 'pack' && slot.assetId && (
                              <AssetChip assetId={slot.assetId} />
                            )}
                            {!fixed && slot.type === 'pack' && filled[0] && (
                              <PackSummary
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
                                className="rounded-md border border-dashed border-line px-1.5 py-0.5 text-[11px] text-ink-mute transition-colors hover:border-primary-line hover:text-primary"
                              >
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
                      <Chip key={item.key}>{item.text}</Chip>
                    ))}
                  </div>
                </PanelSection>
              )}
            </div>

            <div className="flex flex-col gap-2 border-t border-line-soft px-4 py-3">
              {error && <p className="text-[11px] text-danger">{error}</p>}
              {note && (
                <p className="flex items-center gap-2 text-[11px] text-ink-soft">
                  <span className="truncate">{note}</span>
                  <Link to="/tasks" className="shrink-0 text-primary hover:underline">
                    去任务中心
                  </Link>
                </p>
              )}
              <Button block disabled={busy || missing.length > 0} onClick={() => void submit()}>
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
