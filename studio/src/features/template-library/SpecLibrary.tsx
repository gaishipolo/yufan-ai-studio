import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { Play, X } from 'lucide-react';
import { libraryApi } from '@/entities/library/api/library.api';
import { useLibraryDetail } from '@/entities/library/api/library.queries';
import { AssetPreview } from '@/entities/library/ui/AssetPreview';
import { useModels } from '@/entities/model/api/model.queries';
import { useCreateTask } from '@/entities/task/api/task.queries';
import { usePublishedSpecs, useSpecCompile } from '@/entities/template-spec/api/spec.queries';
import type { SpecSlotOut, TemplateSpecOut } from '@/shared/api/schema';
import { AssetPicker, type PickKind, type PickedAsset } from '@/features/template-specs/AssetPicker';
import { Button, Chip, CoverImage, EmptyState, SearchInput, Skeleton } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import { toTaskPayload } from './taskPayload';

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

const TYPE_LABEL: Record<string, string> = {
  image: '图片',
  video: '视频',
  audio: '音频',
  text: '文本',
};

const PICK_KIND: Record<string, PickKind> = {
  image: 'image',
  video: 'video',
  audio: 'audio',
  text: 'text',
};

function slotsOf(spec: TemplateSpecOut | undefined): SpecSlotOut[] {
  return spec?.latest?.slots ?? [];
}

function minOf(slot: SpecSlotOut): number {
  return slot.source === 'pack' ? 1 : (slot.minCount ?? 1);
}

function maxOf(slot: SpecSlotOut): number {
  return slot.source === 'pack' ? 1 : (slot.maxCount ?? 1);
}

/** 缩略图 + 名字。每个 id 一条详情查询，React Query 按 id 缓存，切换模板不重复拉。 */
function AssetChip({assetId, onRemove}: {assetId: string; onRemove?: () => void}) {
  const detail = useLibraryDetail(assetId);
  return (
    <span className="flex items-center gap-1.5 rounded-lg border border-line-soft bg-panel px-1.5 py-1">
      <CoverImage
        src={detail.data?.coverUrl ?? undefined}
        seed={assetId}
        className="h-7 w-7 shrink-0 rounded"
      />
      <span className="max-w-[150px] truncate text-[11px] text-ink-soft">
        {detail.data?.name ?? assetId.slice(0, 8)}
      </span>
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

/**
 * 展示案例：作者上传/选的样例图或视频。
 *
 * 没设就明写「暂无案例」，不摆渐变块冒充缩略图 —— 那会让人以为模板带图。
 * 它是**纯展示**字段（存在草稿里、不进发布快照），所以这里只读、不可交互。
 */
function DemoCase({
  assetId,
  className,
  big = false,
}: {
  assetId: string | null;
  className?: string;
  big?: boolean;
}) {
  if (!assetId) {
    return (
      <div
        className={cn(
          'flex items-center justify-center rounded-lg border border-dashed border-line-strong bg-panel-soft text-[10px] text-ink-mute',
          className,
        )}
      >
        暂无案例
      </div>
    );
  }
  /* 卡片里只给封面（视频带角标），详情里才可播：播放要取整份 blob，
     列表上每张卡都拉一遍视频流不值得。 */
  return <AssetPreview assetId={assetId} className={cn('rounded-lg', className)} playable={big} />;
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
  const videoModels = useModels('video');

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

  const model = videoModels.find(item => item.id === version?.model);
  const defaults = {
    durationSec: Number(model?.paramSchema?.durationSec?.default ?? 0) || undefined,
    aspectRatio: (model?.paramSchema?.aspectRatio?.default as string | undefined) ?? undefined,
  };

  const missing = slots.filter(slot => {
    if (slot.binding !== 'user') return false;
    return (values[slot.key] ?? []).length < minOf(slot);
  });

  /* 提示词里写的是 `{{slot:key}}`，用户看到的是「第几位」。这里按与 compile 同一条规则
     （attachment 不占号、`*` 表示整组）把两者对上，纯展示，不参与提交。 */
  /* 两套编号：视觉参考是 `{{Mixed N}}`（第 N 位），文本是 `{{Text N}}`（第 N 份文案）。 */
  const refLegend = (() => {
    let mixed = 0;
    let text = 0;
    return slots
      .filter(slot => (slot.refMode ?? 'mixed') === 'mixed' || slot.type === 'text')
      .map(slot => {
        if (slot.type === 'text') {
          text += 1;
          return {key: slot.key, at: text, kind: 'text' as const, many: false};
        }
        mixed += 1;
        return {
          key: slot.key,
          at: mixed,
          kind: 'mixed' as const,
          many: maxOf(slot) > 1 || slot.source === 'pack',
        };
      });
  })();
  const busy = compile.isPending || createTask.isPending;

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
      const result = toTaskPayload(compiled, spec.name, defaults);
      if (!result.ok) {
        setError(result.message);
        return;
      }
      const task = await createTask.mutateAsync(result.payload);
      setNote(`已提交：${task.title || task.id}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  if (published.isLoading) {
    return (
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {Array.from({length: 6}).map((_, index) => (
          <Skeleton key={index} className="h-44 rounded-2xl" />
        ))}
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <EmptyState
        title="还没有已发布的模板"
        description="先在「模板规格」里定一张表并发布，它就会出现在这里供填表使用。"
      />
    );
  }

  return (
    <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
      <div className="flex flex-col gap-3">
        <SearchInput value={keyword} onChange={event => setKeyword(event.target.value)} placeholder="按名称筛选" />
        <div className="grid gap-3 sm:grid-cols-2 2xl:grid-cols-3">
          {items.map(item => {
            const itemSlots = slotsOf(item);
            const fixed = itemSlots.filter(slot => slot.binding === 'fixed').length;
            return (
              <button
                key={item.key}
                type="button"
                onClick={() => setSelectedKey(item.key)}
                className={cn(
                  'flex flex-col gap-2 rounded-2xl border p-3 text-left transition-colors',
                  item.key === spec?.key
                    ? 'border-primary-line bg-primary/5'
                    : 'border-line-soft hover:border-primary-line',
                )}
              >
                <DemoCase assetId={item.demoAssetId ?? null} className="h-24 w-full" />
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-[13px] font-medium text-ink">{item.name}</span>
                  <Chip>v{item.currentVersion}</Chip>
                </div>
                <div className="flex flex-wrap gap-1">
                  <Chip>{itemSlots.length} 个槽位</Chip>
                  {fixed > 0 && <Chip>{fixed} 固定</Chip>}
                  {itemSlots.length - fixed > 0 && <Chip>{itemSlots.length - fixed} 待填</Chip>}
                </div>
                <span className="truncate text-[11px] text-ink-mute">
                  {item.latest?.model ?? '未声明模型'}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      <aside className="flex flex-col gap-3 rounded-2xl border border-line-soft bg-panel p-4 xl:sticky xl:top-4">
        {spec && version ? (
          <>
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="truncate text-[15px] font-medium text-ink">{spec.name}</div>
                <div className="mt-0.5 text-[11px] text-ink-mute">
                  v{version.version} · {version.model}
                </div>
              </div>
              <Link to="/templates/specs" className="shrink-0 text-[11px] text-ink-mute hover:text-primary">
                改模板
              </Link>
            </div>

            <DemoCase assetId={spec.demoAssetId ?? null} className="h-40 w-full" big />

            {/* 槽位：fixed 只读展示，user 就地挑素材。纯提示词模板没有槽位，整块不渲染。 */}
            {slots.length > 0 && (
            <section className="flex flex-col gap-2">
              <h3 className="text-[12px] font-medium text-ink-soft">
                填入素材
                {missing.length > 0 && (
                  <span className="ml-1 font-normal text-ink-mute">· 还缺 {missing.length} 项</span>
                )}
              </h3>
              {slots.map((slot, index) => {
                const filled = values[slot.key] ?? [];
                const fixed = slot.binding === 'fixed';
                const locked = fixed && (slot.source === 'pack' ? !!slot.packId : !!slot.assetId);
                return (
                  <div
                    key={slot.key}
                    className="rounded-xl border border-line-soft bg-panel-soft p-2.5"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex min-w-0 items-center gap-1.5">
                        <span className="text-[11px] text-ink-mute">{index + 1}</span>
                        <span className="truncate text-[12px] text-ink">
                          {slot.label || slot.key}
                        </span>
                        <Chip>{TYPE_LABEL[slot.type] ?? slot.type}</Chip>
                        {fixed ? (
                          <Chip>封装固定</Chip>
                        ) : minOf(slot) > 0 ? (
                          <Chip tone="warn">必填</Chip>
                        ) : null}
                      </div>
                      {!fixed && !locked && (
                        <button
                          type="button"
                          onClick={() => setPicker(slot.key)}
                          className="shrink-0 text-[11px] text-primary hover:underline"
                        >
                          {filled.length > 0 ? '更换' : '选择'}
                        </button>
                      )}
                    </div>

                    <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                      {fixed && slot.source === 'pack' && slot.packId && (
                        <PackSummary packId={slot.packId} />
                      )}
                      {fixed && slot.source !== 'pack' && slot.assetId && (
                        <AssetChip assetId={slot.assetId} />
                      )}
                      {!fixed && slot.source === 'pack' && filled[0] && (
                        <PackSummary
                          packId={filled[0]}
                          onRemove={() => setValues(current => ({...current, [slot.key]: []}))}
                        />
                      )}
                      {!fixed &&
                        slot.source !== 'pack' &&
                        filled.map(assetId => (
                          <AssetChip
                            key={assetId}
                            assetId={assetId}
                            onRemove={() =>
                              setValues(current => ({
                                ...current,
                                [slot.key]: (current[slot.key] ?? []).filter(id => id !== assetId),
                              }))
                            }
                          />
                        ))}
                      {!fixed && filled.length === 0 && (
                        <button
                          type="button"
                          onClick={() => setPicker(slot.key)}
                          className="rounded-lg border border-dashed border-line px-2 py-1 text-[11px] text-ink-mute hover:border-primary-line hover:text-primary"
                        >
                          选择{slot.source === 'pack' ? '封装' : (TYPE_LABEL[slot.type] ?? '素材')}
                          {maxOf(slot) > 1 ? `（最多 ${maxOf(slot)}）` : ''}
                        </button>
                      )}
                    </div>
                    {slot.usage && (
                      <p className="mt-1.5 text-[11px] leading-relaxed text-ink-mute">{slot.usage}</p>
                    )}
                  </div>
                );
              })}
            </section>
            )}

            {/* 提示词只有一段：模板库不改文字，要可变就填文本槽位。 */}
            {promptText.trim() && (
              <section className="flex flex-col gap-1.5">
                <h3 className="flex items-center gap-1.5 text-[12px] font-medium text-ink-soft">
                  提示词
                  <span className="text-[10px] font-normal text-ink-mute">不可编辑</span>
                </h3>
                <div className="whitespace-pre-wrap rounded-xl border border-line-soft bg-panel-soft px-2.5 py-2 text-[11px] leading-relaxed text-ink-soft">
                  {promptText}
                </div>
                {refLegend.length > 0 && (
                  <div className="flex flex-wrap items-center gap-1 text-[10px] text-ink-mute">
                    {refLegend.map(item => (
                      <span
                        key={item.key}
                        className="flex items-center gap-1 rounded-md bg-line-soft px-1.5 py-0.5"
                      >
                        <code>{`{{slot:${item.key}${item.many ? '*' : ''}}}`}</code>
                        <span className="text-ink-soft">
                          {item.kind === 'text'
                            ? `第 ${item.at} 份文案`
                            : item.at === 1
                              ? '第 1 位'
                              : `第 ${item.at} 位${item.many ? '起' : ''}`}
                        </span>
                      </span>
                    ))}
                  </div>
                )}
              </section>
            )}

            <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-ink-mute">
              <span>参数</span>
              <Chip>{String(version.params?.aspectRatio ?? defaults.aspectRatio ?? '9:16')}</Chip>
              <Chip>{String(version.params?.durationSec ?? defaults.durationSec ?? '—')} 秒</Chip>
            </div>

            {error && <p className="text-[11px] text-danger">{error}</p>}
            {note && (
              <p className="flex items-center gap-2 text-[11px] text-ink-soft">
                <span>{note}</span>
                <Link to="/tasks" className="text-primary hover:underline">
                  去任务中心
                </Link>
              </p>
            )}

            <Button
              block
              disabled={busy || missing.length > 0}
              onClick={() => void submit()}
            >
              <Play size={13} />
              {busy ? '提交中…' : missing.length > 0 ? `还缺 ${missing.length} 项素材` : '生成'}
            </Button>
          </>
        ) : (
          <p className="text-[12px] text-ink-mute">左侧选一个模板。</p>
        )}
      </aside>

      <AssetPicker
        open={picker !== null}
        kind={pickerSlot?.source === 'pack' ? 'pack' : (PICK_KIND[pickerSlot?.type ?? 'image'] ?? 'image')}
        selected={pickerSlot ? (values[pickerSlot.key] ?? []) : []}
        onClose={() => setPicker(null)}
        onToggle={togglePick}
      />
    </div>
  );
}
