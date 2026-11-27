import { useEffect, useMemo, useState } from 'react';
import { Layers } from 'lucide-react';
import {
  useLibraryPacks,
  useLibraryTags,
} from '@/entities/library/api/library.queries';
import { scopeFilterIds } from '@/entities/library/model/tagTree';
import { viewQuery } from '@/entities/library/model/types';
import { TagFilter } from '@/entities/library/ui/TagFilter';
import type { SpecSlotOut } from '@/shared/api/schema';
import { CoverImage, Segmented } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import { AssetChip } from '@/entities/library/ui/AssetChip';
import { PackHoverPreview } from '@/entities/library/ui/PackHoverPreview';
import { SlotTextField } from './SlotTextField';
import type { RefChip } from '@/features/template-specs/specDraft';

/**
 * 封装容器在**模板库**这边的形态。
 *
 * 作者把几个槽位放进同一个容器（槽位上的 `group`），使用者看到的就是**一个**
 * 选包入口：上面一格一格的包，点一次，按标签把成员分到下面这几格。
 *
 * 为什么不复用 `SlotMediaPicker`：那个组件的粒度是「一个槽位」，而容器的
 * 语义是「一次选择，多格一起定」。硬塞进去会有两套计数、两个「已选」，
 * 正好是用户报的那个「点了只一次，但显示还是两个」。
 */
export function PackGroupPicker({
  label,
  members,
  values,
  canEditText,
  slotModes,
  textOf,
  onMode,
  onText,
  onPickPack,
  onClear,
  chips,
  authorTexts,}: {
  /** 容器名（作者起的 / 没起名时按段序的默认名）。 */
  label: string;
  /** 容器里的槽位（顺序即编号顺序）。 */
  members: SpecSlotOut[];
  values: Record<string, string[] | undefined>;
  /** 可以「改提示词」的成员 key（可选 + 提示词有插入点 + 没被「第 N 张」钉住）。 */
  canEditText: string[];
  /** 每格当前的填写方式。缺省 = 选素材。 */
  slotModes: Record<string, 'pick' | 'text'>;
  /** 某格改写的文案（父层已填成作者的「未选时文案」）。 */
  textOf: (key: string) => string;
  onMode: (key: string, mode: 'pick' | 'text') => void;
  onText: (key: string, value: string) => void;
  onPickPack: (packId: string) => void;
  /** 胶囊上的槽位名用：把整个模板的槽位传下来（引用可能指向别的槽位）。 */
  chips?: RefChip[];
  /** 各成员作者写的原文：允许保留的引用以它为准。 */
  authorTexts?: Record<string, string>;
  /** 清掉某一格（chip 上的 ×）。 */
  onClear: (slotKey: string) => void;
}) {
  const [hoveredPackId, setHoveredPackId] = useState<string | null>(null);
  const [pointer, setPointer] = useState<{x: number; y: number} | null>(null);

  /* 容器的候选标签 = 子槽位候选标签的并集（自动继承）。
     例如「左图=产品+正面图，右图=产品+三视图」→ 产品 + 正面图 + 三视图。 */
  const scope = useMemo(
    () => Array.from(new Set(members.flatMap(member => member.tagIds ?? []))),
    [members],
  );
  const scopeKey = scope.join(',');
  /* 跟槽位选择器同一套：列的是**这批候选范围里实际有值**的标签，
     用户点一个就在范围里再筛（同维度替换）。一级维度本身不出现在行里。 */
  const tags = useLibraryTags({
    ...viewQuery('all'),
    ...(scope.length ? {tagIds: scope} : {}),
    // 候选集里挑的是**成员**（选择器不藏封装成员）→ 计数按成员算。
    includePackMembers: true,
  });
  const [picked, setPicked] = useState<string[]>([]);
  /* 换模板 / 换容器 = 换候选范围：上次挑的标签可能已经在范围外。 */
  useEffect(() => {
    setPicked([]);
  }, [scopeKey]);

  const filterIds = useMemo(
    () => scopeFilterIds(tags.data ?? [], scope, picked),
    [tags.data, scope, picked],
  );
  const childCountOf = useMemo(() => {
    const map = new Map((tags.data ?? []).map(tag => [tag.id, tag.childCount ?? 0]));
    return (id: string) => map.get(id) ?? 0;
  }, [tags.data]);
  /* **只把「能打在素材上」的标签发去需要求**：一级标签是维度（产品 / 产品图类型），
     素材身上挂的是它的叶子。把维度也放进 `mode=all` 里，等于要求每个成员都挂
     「产品」这个一级标签 —— 一个包都匹配不上。 */
  const required = filterIds.filter(id => childCountOf(id) === 0);

  /* `mode=all`：容器里有几格，包就得几张对得上 —— 只对得上一张的包不列，
     否则点下去只填一半，而界面上没有地方解释为什么少一格。 */
  const packs = useLibraryPacks(
    {tagIds: required.length ? required : undefined, mode: 'all'},
    scope.length > 0,
  );
  const rows = packs.data ?? [];
  /* 文案格也算「填了」：容器那行 `x/y` 说的是「还差几格」，
     差的是「没有任何说法」的格子，不是「没有素材」的格子。 */
  const isTextMode = (key: string) =>
    canEditText.includes(key) && slotModes[key] === 'text';
  const filled = members.filter(
    member =>
      (values[member.key] ?? []).length > 0 ||
      (isTextMode(member.key) && textOf(member.key).trim().length > 0),
  ).length;

  return (
    <section className="rounded-xl border border-[rgba(211,224,229,.7)] bg-canvas/45 p-2.5 shadow-[inset_0_1px_0_rgba(255,255,255,.9)]">
      <div className="flex items-baseline gap-1.5">
        <span className="flex items-center gap-1 text-xs font-medium text-ink">
          <Layers size={12} className="text-primary" aria-hidden />
          {/* 作者起的名字。没起名时调用方已按段序回退成「封装槽位1/2/…」，
             这里不再叠一层判断 —— 两处各算一次就会出现「作者看到 A、使用者看到 B」。 */}
          <span className="line-clamp-1">{label}</span>
        </span>
        <span
          className={cn(
            'text-micro',
            filled === members.length ? 'text-ok' : 'text-warn-deep',
          )}
        >
          {filled}/{members.length}
        </span>
        <span className="ml-auto shrink-0 text-micro text-ink-mute">
          {scope.length ? '选一个包，这几格一起填好' : '这些槽位还没圈候选标签'}
        </span>
      </div>

      {/* 容器的筛选行 = 子槽位候选标签的并集，作用与普通槽位那一行相同：
          自动圈定范围、手动再收窄。二级标签（6036 / 正面图 / 三视图）都在这里选。 */}
      {scope.length > 0 && (
        <div className="mt-1.5">
          <TagFilter
            tags={tags.data ?? []}
            onlyWithAssets
            hideTopLevel
            excludeIds={scope}
            single
            value={picked}
            onChange={setPicked}
          />
        </div>
      )}

      {scope.length > 0 && rows.length === 0 ? (
        <p className="py-3 text-center text-meta text-ink-mute">
          {picked.length
            ? '没有同时含这几张的封装包。换个产品再试，或去资产库把这个产品的那几张打成一个包。'
            : '这批标签下还没有封装包。去资产库把要一起用的几张打成一个封装。'}
        </p>
      ) : (
        <div className="mt-2 grid grid-cols-4 gap-1.5">
          {rows.map(pack => (
            <button
              key={pack.id}
              type="button"
              title={`${pack.name}（封装 · ${pack.memberCount ?? 0} 张）`}
              onClick={() => onPickPack(pack.id)}
              onMouseEnter={event => {
                setHoveredPackId(pack.id);
                setPointer({x: event.clientX, y: event.clientY});
              }}
              onMouseMove={event => setPointer({x: event.clientX, y: event.clientY})}
              onMouseLeave={() => {
                setHoveredPackId(null);
                setPointer(null);
              }}
              className="group relative aspect-square overflow-hidden rounded-lg border border-line-soft transition hover:border-primary-line"
            >
              <CoverImage src={pack.coverUrl ?? undefined} seed={pack.id} className="h-full w-full" />
              <span className="absolute inset-x-0 bottom-0 flex items-center justify-center gap-0.5 bg-[rgba(12,24,30,.62)] py-px text-[9px] leading-[11px] text-white">
                <Layers size={8} />
                {pack.memberCount ?? 0} 张
              </span>
            </button>
          ))}
        </div>
      )}

      {/* 每格一行：填了就显示那张（可撤），没填写「可留空 / 待填」。
          这里不摆网格 —— 容器里的槽位只由包决定，给它们各自的网格等于
          把「一次选择」拆回「两次选择」。

          但**可选**的成员可以改成「改提示词」：包只填它那几格，剩下的格子
          由使用者自己描述。切成文案的格子不再由包分发（见
          `SpecLibrary.distributePack`）。 */}
      <ul className="mt-2 flex flex-col gap-1.5">
        {members.map(member => {
          const picked = values[member.key] ?? [];
          const editable = canEditText.includes(member.key);
          const textMode = isTextMode(member.key);
          return (
            <li key={member.key} className="flex flex-col gap-1 text-micro">
              {/* `flex-wrap`：一格既有已选素材、又有填法切换时，两个控件在
                  400px 的右栏里可能挤不下 —— 换行比挤出容器好。 */}
              <div className="flex flex-wrap items-center gap-2">
                <span className="w-16 shrink-0 truncate text-ink-soft">
                  {member.label || member.key}
                </span>
                {/* 文案格不再显示「可留空 / 待填」：下面那个输入框就是它的说法。 */}
                {!textMode &&
                  (picked.length ? (
                    <AssetChip assetId={picked[0]} onRemove={() => onClear(member.key)} />
                  ) : (
                    <span className="text-ink-mute">
                      {member.minCount === 0 ? '可留空' : '待填'}
                    </span>
                  ))}
                {editable && (
                  <Segmented
                    aria-label={`${member.label || member.key} 的填法`}
                    value={textMode ? 'text' : 'pick'}
                    onChange={next => onMode(member.key, next)}
                    options={[
                      {value: 'pick', label: '选素材'},
                      {value: 'text', label: '改提示词'},
                    ]}
                    className="ml-auto w-[136px] shrink-0"
                  />
                )}
              </div>
              {textMode && (
                <SlotTextField
                  key={member.key}
                  rows={2}
                  value={textOf(member.key)}
                  onChange={value => onText(member.key, value)}
                  placeholder="写这一格没素材时提示词怎么说"
                  chips={chips}
                  authorText={authorTexts?.[member.key]}
                  ariaLabel={`${member.label || member.key} 的改提示词`}
                />
              )}
            </li>
          );
        })}
      </ul>

      <PackHoverPreview packId={hoveredPackId} pointer={pointer} />
    </section>
  );
}
