import { useEffect, useMemo, useState } from 'react';
import { Check } from 'lucide-react';
import {
  useLibrarySelection,
  useLibrarySelectionInfinite,
  useLibraryTags,
} from '@/entities/library/api/library.queries';
import { scopeFilterIds } from '@/entities/library/model/tagTree';
import { KIND_LABEL, viewQuery } from '@/entities/library/model/types';
import type { SpecSlotOut } from '@/shared/api/schema';
import { CoverImage, Segmented } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import { AssetChip } from '@/entities/library/ui/AssetChip';
import { AssetHoverPreview } from '@/entities/library/ui/AssetHoverPreview';
import { TagFilter } from '@/entities/library/ui/TagFilter';
import { pickWithin } from './slotPick';
import { SlotTextField } from './SlotTextField';

/* 首屏取 24 张，「显示全部」按**服务端单页上限**翻页取全。
 *
 * 之前这里写 `PAGE_SIZE_MAX = 200`，注释还声称与服务端 `le` 相同 —— 实际服务端
 * 一直是 100。127 张候选点「显示全部」会发 `pageSize=127` 直接 400，界面上
 * 所有素材凭空消失（2026-11-22）。翻页取全既能显示超过 100 张，也不会再撞上限。
 */
const PAGE_SIZE = 24;
const ALL_PAGE_SIZE = 100;

export function SlotMediaPicker({
  slot,
  selected,
  onChange,
  requiredCount,
  mode = 'pick',
  canEditText = false,
  text = '',
  onModeChange,
  onTextChange,
}: {
  slot: SpecSlotOut;
  selected: string[];
  onChange: (ids: string[]) => void;
  /** 这个槽位实际最少要几张（作者写的 `minCount`，或提示词「第 N 张」推出的下限）。 */
  requiredCount?: number;
  /** 填写方式：`pick` 挑素材（默认），`text` 直接改写提示词里那一格。 */
  mode?: 'pick' | 'text';
  /** 能不能切到「改提示词」。不能就连分段控件都不画 —— 见 `hasFallbackSlotRef`。 */
  canEditText?: boolean;
  /** 改写的文案（父层已填成作者的「未选时文案」）。 */
  text?: string;
  onModeChange?: (mode: 'pick' | 'text') => void;
  onTextChange?: (text: string) => void;
}) {
  const kind = slot.type as 'image' | 'video';
  const label = KIND_LABEL[kind];
  /* 这个选择器**只列素材**：进封装的槽位在模板库里由 `PackGroupPicker` 画
     （一个选包入口），走不到这里。「来源」不再是槽位的字段 —— 用哪条链路
     由 `group` 决定（见 `docs/TEMPLATE_SPEC.md` §10.5）。 */
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
    // 这里挑的是**成员**（选择器不藏封装成员）→ 计数按成员算。
    includePackMembers: true,
  });
  /* 用户在这个槽位里又挑的标签（可空）：同维度是或、跨维度是且。 */
  const [picked, setPicked] = useState<string[]>([]);
  /* 是否翻完全部。换槽位 / 换候选集时回到首屏 —— 上一格点过「显示全部」，
     不该让下一格一上来就发一串翻页请求。 */
  const [showAll, setShowAll] = useState(false);
  /* 换槽位 = 换候选集，之前挑的标签可能已经在范围外：清掉重来。 */
  useEffect(() => {
    setPicked([]);
    setShowAll(false);
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

  const selectionParams = {
    kind,
    tagIds: filterIds.length ? filterIds : undefined,
  };
  const list = useLibrarySelection({ ...selectionParams, pageSize: PAGE_SIZE });
  /* 「显示全部」走翻页：每页最多 100，和后端 PageDep 的 le 一致。
     不在首屏挂 enabled，避免每个槽位挂载就多打一串请求。 */
  const all = useLibrarySelectionInfinite({
    ...selectionParams,
    pageSize: ALL_PAGE_SIZE,
    enabled: showAll,
  });
  const {hasNextPage, isFetchingNextPage, fetchNextPage} = all;
  useEffect(() => {
    if (showAll && hasNextPage && !isFetchingNextPage) {
      void fetchNextPage();
    }
  }, [showAll, hasNextPage, isFetchingNextPage, fetchNextPage]);

  const items = showAll
    ? (all.data?.pages.flatMap((page) => page.items ?? []) ?? [])
    : (list.data?.items ?? []);
  /* 这一档标签下还有多少张没列出来（标题里的「共 N」是总数）。 */
  const hiddenCount = showAll ? 0 : Math.max(0, (list.data?.total ?? 0) - items.length);


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
  /* 只有父层说「这一格能改文案」时 `text` 才算数：换模板/换版本后残留的
     状态不该让一个没有插入点的槽位画出一个打了字也不生效的输入框。 */
  const editingText = canEditText && mode === 'text';

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
        {/* 改成提示词时不再数素材：这一刻没有「已选几张」这回事，
            数字回读交给输入框下面那行。 */}
        {!editingText && (
          <span className="ml-auto shrink-0 text-micro text-ink-mute">
            {/* 单选也用同一套计数回读（`已选 0/1`），而不是另写一句「选 1 个」——
                两套写法让同一件事长得不一样，而且「选 1 个」在多选时没有对应说法。 */}
            {label} · 已选 {selected.length}/{max}
            {/* 提示词里的「第 N 张」推出的下限可能**超过上限**（作者配置错了）。
                那时候写「已选 0/1」是骗人的 —— 照实说，别让使用者以为挑一张就够。 */}
            {need > max && ` · 至少 ${need} 张`}
            {typeof list.data?.total === 'number' &&
              filterIds.length > 0 &&
              ` · 共 ${list.data.total}`}
          </span>
        )}
      </div>

      {/* 填法：挑素材 / 直接写提示词里这一格。只在**这一格真有插入点**时出现
          （`hasFallbackSlotRef`）—— 没有插入点时敲的字不会进提示词。 */}
      {canEditText && (
        <Segmented
          aria-label={`${slot.label || slot.key} 的填法`}
          value={editingText ? 'text' : 'pick'}
          onChange={next => onModeChange?.(next)}
          options={[
            {value: 'pick', label: '选素材'},
            {value: 'text', label: '改提示词'},
          ]}
          hints={{
            pick: '从资产库里挑素材（也可以不挑）',
            text: '不挑素材，直接写这一格没素材时怎么说',
          }}
          className="mt-1.5"
        />
      )}

      {editingText ? (
        <SlotTextField
          className="mt-2"
          value={text}
          onChange={value => onTextChange?.(value)}
          placeholder="写这一格没素材时提示词怎么说"
        />
      ) : (
        <>

      {/* 可选槽位**不摆**「不填会怎样」的说明：这栏由「随机性」三档控制，
          可选槽位要么不出现（随机/均衡档，走文案），要么出现由使用者挑（自定义）。
          「不填则用：…」是**填表页**的旧形态 —— 同一件事说两遍，
          而且它描述的状态（不填）在新形态下已经不成立了。 */}

      {!scope.length && (
        /* 老模板（闸门加上之前发布的）没有标签：仍然能挑，但要说明为什么没有分组。 */
        <p className="mt-1 text-micro leading-relaxed text-warn-deep">
          这个槽位还没圈候选标签（去「模板封装」补一个，这一格才能只列那批素材），
          下面是全部{label}。
        </p>
      )}

      {/* 已选的素材不再单独列一排 chip：网格里打了勾、右上角还写着「已选 2/3」，
          再列一遍只是重复。只留"选了但不在当前这档标签里"的那些 —— 它们在网格中
          看不见，不留 chip 就只能靠切回原标签才删得掉（满额时还会卡死：全部置灰）。 */}
      {hiddenPicked.length > 0 && (
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
                  onMouseEnter={event => {
                    setHoveredId(item.id);
                    setPointer({ x: event.clientX, y: event.clientY });
                  }}
                  onMouseMove={event => setPointer({ x: event.clientX, y: event.clientY })}
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

      {/* 「共 N 张」里的那 N 张必须真的都能点到。首屏只取 24 张是为了不一次性
          发几十个缩略图请求，但之前没有这一行 —— 第 25 张以后在界面上不存在，
          标题却写着总数，用户只会以为那张图不在库里。 */}
      {hiddenCount > 0 && (
        <button
          type="button"
          onClick={() => setShowAll(true)}
          className="mt-1.5 w-full rounded-md py-1 text-micro text-primary transition-colors hover:bg-primary-soft"
        >
          还有 {hiddenCount} 张 · 显示全部
        </button>
      )}
      {showAll && isFetchingNextPage && (
        <p className="mt-1 text-center text-micro text-ink-mute">正在载入更多…</p>
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
        </>
      )}
    </div>
  );
}
