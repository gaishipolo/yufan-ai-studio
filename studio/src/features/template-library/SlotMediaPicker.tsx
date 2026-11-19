import { useEffect, useMemo, useState } from 'react';
import { Check } from 'lucide-react';
import {
  useLibrarySelection,
  useLibraryTags,
} from '@/entities/library/api/library.queries';
import { scopeFilterIds } from '@/entities/library/model/tagTree';
import { KIND_LABEL, viewQuery } from '@/entities/library/model/types';
import type { SpecSlotOut } from '@/shared/api/schema';
import { CoverImage } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import { AssetChip } from '@/entities/library/ui/AssetChip';
import { AssetHoverPreview } from '@/entities/library/ui/AssetHoverPreview';
import { TagFilter } from '@/entities/library/ui/TagFilter';
import { pickWithin } from './slotPick';

const PAGE_SIZE = 24;

export function SlotMediaPicker({
  slot,
  selected,
  onChange,
  requiredCount,
}: {
  slot: SpecSlotOut;
  selected: string[];
  onChange: (ids: string[]) => void;
  /** 这个槽位实际最少要几张（作者写的 `minCount`，或提示词「第 N 张」推出的下限）。 */
  requiredCount?: number;
}) {
  const kind = slot.type as 'image' | 'video';
  const label = KIND_LABEL[kind];
  const max = Math.max(1, slot.maxCount ?? 1);
  /** 实际下限。缺省时就是作者写的 `minCount`。 */
  const need = requiredCount ?? (slot.minCount ?? 1);
  /** 槽位的候选集（一组标签，任意级别）。空 = 不限。 */
  const scope = useMemo(() => slot.tagIds ?? [], [slot.tagIds]);
  const scopeKey = scope.join(',');

  /*
   * 标签词表按**候选范围**收窄：只统计符合候选集的那批素材。
   *
   * 这一步决定了界面上能点什么：模板库要显示的是「这批素材身上实际有的
   * 二级标签」，不是整个词表。按全库拿会列出一堆这批素材一个都没挂的标签，
   * 用户点了只得到空列表。
   */
  const tags = useLibraryTags({
    ...viewQuery('all'),
    kind,
    ...(scope.length ? { tagIds: scope } : {}),
  });
  /* 用户在这个槽位里又挑的标签（可空）：同维度是或、跨维度是且。 */
  const [picked, setPicked] = useState<string[]>([]);
  /* 换槽位 = 换候选集，之前挑的标签可能已经在范围外：清掉重来。 */
  useEffect(() => {
    setPicked([]);
  }, [scopeKey, slot.key]);

  /*
   * 实际发给后端的标签：**候选集 + 使用者的挑选**，按维度替换。
   *
   * 使用者的点击是「在候选集里再筛」，不是「再限一层」——
   * 候选集圈了「产品 + 三视图」，使用者挑「产品 / 6036」，
   * 筛的是「既是 6036 又是三视图」，而不是要求素材同时挂三样。
   */
  const filterIds = useMemo(
    () => scopeFilterIds(tags.data ?? [], scope, picked),
    [tags.data, picked, scope],
  );

  const list = useLibrarySelection({
    kind,
    tagIds: filterIds.length ? filterIds : undefined,
    pageSize: PAGE_SIZE,
  });
  const items = list.data?.items ?? [];

  /* 已选但不在当前这档标签下的素材（换标签之后才会出现），见下面的渲染注释。 */
  const shown = new Set(items.map(item => item.id));
  const hiddenPicked = selected.filter(id => !shown.has(id));

  /*
   * 悬浮大图：缩略图太小看不清产品细节，悬停时另浮一张大图。
   *
   * **点击仍然是选中** —— 浮层是 `pointer-events-none`，不接管点击。
   */
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [pointer, setPointer] = useState<{ x: number; y: number } | null>(null);
  const hovered = items.find(item => item.id === hoveredId);

  const toggle = (assetId: string) => {
    const next = pickWithin(selected, assetId, max);
    if (next !== selected) onChange(next);
  };

  const full = selected.length >= max;

  return (
    // 槽位是「卡片里的凹槽」：比面板暗一档、带内描边，里面的缩略图才有地方可放。
    <div className="rounded-xl border border-[rgba(211,224,229,.7)] bg-canvas/45 p-2.5 shadow-[inset_0_1px_0_rgba(255,255,255,.9)]">
      <div className="flex items-baseline gap-1.5">
        <span className="min-w-0 truncate text-xs font-medium text-ink">
          {slot.label || slot.key}
        </span>
        {/* 星号标的是「**还没填够**」，不是「有下限」。
            之前标的是「有下限」—— 一个填好的必填槽位和一个空着的长得一样，
            用户只能自己逐格核对，或点了提交再吃一个错误。

            `aria-label` 让读屏能念出它的意思（光标一个星号读不出来），
            也给测试一个稳定的定位点。 */}
        {need > selected.length && (
          <span className="text-warn-deep" aria-label={`${slot.label || slot.key} 还没填够`}>
            *
          </span>
        )}
        <span className="ml-auto shrink-0 text-micro text-ink-mute">
          {/* 单选也用同一套计数回读（`已选 0/1`），而不是另写一句「选 1 个」——
              两套写法让同一件事长得不一样，而且「选 1 个」在多选时没有对应说法。 */}
          {label} · 已选 {selected.length}/{max}
          {/* 提示词里的「第 N 张」推出的下限可能**超过上限**（作者配置错了）。
              那时候写「已选 0/1」是骗人的 —— 照实说，别让使用者以为挑一张就够。 */}
          {need > max && ` · 至少 ${need} 张`}
          {typeof list.data?.total === 'number' && filterIds.length > 0 && ` · 共 ${list.data.total}`}
        </span>
      </div>

      {/* 可选槽位**不摆**「不填会怎样」的说明：这栏由「随机性」三档控制，
          可选槽位要么不出现（随机/均衡档，走文案），要么出现由使用者挑（自定义）。
          「不填则用：…」是**填表页**的旧形态 —— 同一件事说两遍，
          而且它描述的状态（不填）在新形态下已经不成立了。 */}

      {!scope.length && (
        /* 老模板（闸门加上之前发布的）没有标签：仍然能挑，但要说明为什么没有分组。 */
        <p className="mt-1 text-micro leading-relaxed text-warn-deep">
          这个槽位还没圈标签（去「模板封装」补一个才能按标签分组），下面是全部{label}。
        </p>
      )}

      {/* 已选的素材不再单独列一排 chip：网格里打了勾、右上角还写着「已选 2/3」，
          再列一遍只是重复。只留"选了但不在当前这档标签里"的那些 —— 它们在网格中
          看不见，不留 chip 就只能靠切回原标签才删得掉（满额时还会卡死：全部置灰）。 */}
      {list.isSuccess && hiddenPicked.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {hiddenPicked.map(assetId => (
            <AssetChip
              key={assetId}
              assetId={assetId}
              onRemove={() => onChange(selected.filter(id => id !== assetId))}
            />
          ))}
        </div>
      )}

      {/* 标签筛选：列的是**这批候选素材身上实际有**的标签，
          用户点了就在候选集里再筛一层。

          **单选** —— 候选集已经把范围圈死了，用户在范围内只想挑一档看看，
          多选没有意义。

          一级维度行不显示（那只是分组头，范围已经由候选集定死）；
          候选集里的标签本身也不列 —— 它就是范围，列出来点了等于没筛。 */}
      <div className="mt-2">
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

      {tags.isPending ? (
        <p className="py-4 text-center text-meta text-ink-mute">正在读取标签…</p>
      ) : list.isLoading ? (
        <p className="py-4 text-center text-meta text-ink-mute">正在读取素材…</p>
      ) : items.length === 0 ? (
        <p className="py-4 text-center text-meta text-ink-mute">
          {scope.length ? '这个标签下还没有素材，去资产库给它打上标签。' : `还没有${label}。`}
        </p>
      ) : (
        <div className="mt-2 grid max-h-[188px] grid-cols-4 gap-1.5 overflow-y-auto pr-0.5">
          {items.map(item => {
            const isPicked = selected.includes(item.id);
            const blocked = full && !isPicked && max > 1;
            return (
              <button
                key={item.id}
                type="button"
                title={item.name}
                disabled={blocked}
                onClick={() => toggle(item.id)}
                /* 鼠标事件挂在**按钮**上（不是里面的图）：缩略图只占按钮的一部分，
                   挂在内层会让鼠标滑到边框时浮层闪一下。 */
                onMouseEnter={(event) => {
                  setHoveredId(item.id);
                  setPointer({ x: event.clientX, y: event.clientY });
                }}
                onMouseMove={(event) =>
                  setPointer({ x: event.clientX, y: event.clientY })
                }
                onMouseLeave={() => {
                  setHoveredId(null);
                  setPointer(null);
                }}
                className={cn(
                  'group relative aspect-square overflow-hidden rounded-lg border transition',
                  isPicked
                    ? 'border-primary-line ring-2 ring-primary/25'
                    : 'border-line-soft hover:border-line-strong',
                  blocked && 'cursor-not-allowed opacity-40',
                )}
              >
                <CoverImage
                  src={item.coverUrl ?? undefined}
                  seed={item.id}
                  className="h-full w-full"
                />
                {isPicked && (
                  <span className="absolute right-1 top-1 grid size-4 place-items-center rounded-full bg-primary text-white">
                    <Check size={10} strokeWidth={3} />
                  </span>
                )}
                {item.kind === 'video' && (
                  <span className="absolute bottom-1 left-1 rounded bg-black/55 px-1 text-micro text-white">
                    视频
                  </span>
                )}
              </button>
            );
          })}
        </div>
      )}

      {/* 悬浮大图：点一下仍然是选中（浮层不接管点击）。
          宽高要一起传：竖图（三视图）与横图该占的高度差好几倍，
          不给比例就只能一刀切限高，竖图会被裁掉一半。 */}
      <AssetHoverPreview
        assetId={hoveredId}
        name={hovered?.name}
        width={hovered?.width}
        height={hovered?.height}
        pointer={pointer}
      />
    </div>
  );
}
