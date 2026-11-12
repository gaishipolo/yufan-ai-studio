/**
 * 素材库侧边悬浮栏：给生成表单「就地挑素材」用。
 *
 * 与 `LibraryPicker`（弹窗 + 多选 + 确认）的分工：
 * - 弹窗版适合「一次挑一批，挑完继续在别处操作」；
 * - 悬浮栏适合「一边看着右边的结果、一边把素材拖进左边的表单」——弹窗会遮住
 *   整个页面，拖拽根本没法落到被遮住的目标上，所以这里**不加遮罩**。
 *
 * 只打 `GET /library/selection`（后端硬过滤 `state=active`）：草稿与回收站里的
 * 东西绝不能进生成任务，这不是一个前端能关掉的筛选。
 *
 * 默认只看图片：参考图只收 png/jpeg/webp，把视频和音频摆出来只会让人拖了才被拒。
 *
 * ## 四个类别 + 两级标签（2026-11-11）
 *
 * 抽屉顶部先给**图片 / 成片 / 音频 / 文本**四个类别，再给**一级 / 二级**两行标签。
 * 之前这里只有一行扁平胶囊（二级显示成「父 / 子」），看不出层级，也没法按类别看库。
 *
 * 1. 类别都能点开看库，但**接收方不认的类别只浏览**：卡片上没有「＋」、也不可拖，
 *    并给一行说明。直接置灰会让人以为库里没这类素材；能拖走再被拒更糟。
 * 2. 标签仍是**单选**（抽屉太窄，多选会挤成三行把素材顶出视野）。一级点下去看
 *    该组全部（后端把一级展开成「自身 + 旗下二级」）；点了二级就把一级从条件里
 *    换成二级，避免 OR 叠加后等于没筛。
 * 3. 当前一级**从选中派生**，不另存一份 state：刷新/切换类别后两行仍对得上。
 * 4. 标签胶囊的计数**按当前类别算**（`/library/tags?kind=…`）：抽屉切到「图片」
 *    就显示图片的条数。写成全库口径的话，胶囊写着 9、点进去只有 4 条。
 */
import { useMemo, useState } from 'react';
import { Check, Library, Plus, X } from 'lucide-react';
import { Button, Chip, CoverImage, SearchInput, Skeleton } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import { useDebouncedValue } from '@/shared/hooks/useDebouncedValue';
import type {
  LibraryAssetKind,
  LibraryAssetOut,
  LibraryTagStatOut,
} from '@/shared/api/schema';
import { useLibrarySelectionInfinite, useLibraryTags } from '../api/library.queries';
import { KIND_OPTIONS, displayName } from '../model/types';
import { childTags, tagLabel, topLevelTags } from '../model/tagTree';
import { writeLibraryDrag, type LibraryAssetDrag } from '@/shared/lib/libraryDrag';

/** 默认只看图片：文本资产默认不进「挑参考图」的悬浮栏，需要时由调用方显式放开 */
const DEFAULT_FLYOUT_KINDS: LibraryAssetKind[] = ['image'];

/** 四个素材类别：与资产库页的类别口径同一份来源，别在这里另抄一套文案 */
const CATEGORIES = KIND_OPTIONS.filter(
  (item): item is { value: LibraryAssetKind; label: string } => item.value !== 'all',
);

export interface LibraryFlyoutProps {
  onClose: () => void;
  /** 接收方声明的上限（模型的 maxReferences） */
  max: number;
  /** 已经在参考图列表里的素材 id，顺序即最终生效顺序 */
  pickedIds: string[];
  /** 点击「＋」加入。拖拽走 `drag.ts` 的载荷，不经过这里 */
  onPick: (asset: LibraryAssetDrag) => void;
  /** 允许出现的类型；不传默认只有 image。显式含 'text' 才显示文本资产 */
  kinds?: LibraryAssetKind[];
}

/** 缩略图优先封面；没有封面就退回预览地址，再没有就交给 CoverImage 画占位 */
function dragPayloadOf(asset: LibraryAssetOut): LibraryAssetDrag {
  return {
    id: asset.id,
    name: asset.name,
    url: asset.coverUrl ?? asset.previewUrl ?? asset.url ?? '',
  };
}

/**
 * 一级 / 二级共用的一行标签：行标题 + 「全部」+ 单选胶囊（计数取当前入口）。
 *
 * 抽屉只有 318px，行内横滑而不是换行：换行会把素材网格顶下去，
 * 而两级同时展开时行数已经翻倍了。
 */
function FlyoutTagRow({
  label,
  ariaLabel,
  allLabel,
  allActive,
  onAll,
  items,
  activeId,
  onPick,
  showParentName,
  hint,
}: {
  label: string;
  ariaLabel: string;
  allLabel: string;
  allActive: boolean;
  onAll: () => void;
  items: LibraryTagStatOut[];
  activeId: string;
  onPick: (id: string) => void;
  /** 未选一级时二级行会把同名的两组混在一起，带上父名才分得清 */
  showParentName?: boolean;
  /** 该组下没有可选项时的一行说明（保留行本身，避免二级行突然消失） */
  hint?: string;
}) {
  return (
    <div
      role="group"
      aria-label={ariaLabel}
      className="flex items-center gap-1 overflow-x-auto pb-0.5"
    >
      <span className="w-7 shrink-0 truncate text-[10.5px] font-medium text-ink-mute" title={label}>
        {label}
      </span>
      <button
        type="button"
        onClick={onAll}
        aria-label={allLabel}
        aria-pressed={allActive}
        className={cn(
          'shrink-0 rounded-full border px-2 py-[3px] text-[10.5px] transition',
          allActive
            ? 'border-primary-line bg-primary-soft text-primary-deep'
            : 'border-line-soft bg-panel-soft text-ink-soft hover:border-line-strong',
        )}
      >
        全部
      </button>
      {items.map((tag) => {
        const text = showParentName ? tagLabel(tag) : tag.name;
        const active = activeId === tag.id;
        return (
          <button
            key={tag.id}
            type="button"
            onClick={() => onPick(tag.id)}
            aria-pressed={active}
            title={`筛选「${text}」· 当前入口 ${tag.assetCount ?? 0} 条`}
            className={cn(
              'shrink-0 rounded-full border px-2 py-[3px] text-[10.5px] transition',
              active
                ? 'border-primary-line bg-primary-soft font-semibold text-primary-deep'
                : 'border-line-soft bg-panel-soft text-ink-soft hover:border-line-strong',
            )}
          >
            <span className="max-w-[110px] truncate">{text}</span>
            <span className="ml-1 text-ink-mute">{tag.assetCount ?? 0}</span>
          </button>
        );
      })}
      {hint && <span className="shrink-0 pl-0.5 text-[10.5px] text-ink-mute">{hint}</span>}
    </div>
  );
}

export function LibraryFlyout({
  onClose,
  max,
  pickedIds,
  onPick,
  kinds = DEFAULT_FLYOUT_KINDS,
}: LibraryFlyoutProps) {
  const [keyword, setKeyword] = useState('');
  /** 类别单选：多选传到服务端就分不了页，而这里必须能「加载更多」 */
  const [kind, setKind] = useState<LibraryAssetKind>(kinds[0] ?? 'image');
  const [topTagId, setTopTagId] = useState('');
  const [childTagId, setChildTagId] = useState('');
  const debounced = useDebouncedValue(keyword, 250);

  // 标签计数按入口变化；这里固定「全部素材」口径，与选择器的可见集一致。
  // **再按当前类别收窄**：抽屉顶部已经切成「图片 / 成片 / 音频 / 文本」，
  // 胶囊上的数字却还是全类的——写着 9、点进去只有 4 条，用户以为数据丢了。
  const tagScope = useMemo(
    () => ({ state: 'active' as const, library: 'all' as const, kind }),
    [kind],
  );
  const tags = useLibraryTags(tagScope);

  const allTags = useMemo(() => tags.data ?? [], [tags.data]);
  const top = useMemo(() => topLevelTags(allTags), [allTags]);
  const byId = useMemo(() => new Map(allTags.map((tag) => [tag.id, tag])), [allTags]);

  /** 当前一级：优先选中的一级，其次「选中二级的父」——与标签栏同一套派生口径 */
  const activeParent = useMemo(() => {
    if (topTagId) return topTagId;
    return childTagId ? (byId.get(childTagId)?.parentId ?? '') : '';
  }, [topTagId, childTagId, byId]);
  const children = useMemo(
    () => childTags(allTags, activeParent || undefined),
    [allTags, activeParent],
  );
  /** 词表里有没有二级：一个都没有时二级行退化成一句说明，而不是假装有 */
  const hasAnyChild = useMemo(() => allTags.some((tag) => !!tag.parentId), [allTags]);

  /* 二级比一级更具体：选中二级就不再带一级，否则 OR 叠加后仍是整组，看着像没筛 */
  const effectiveTag = childTagId || topTagId;
  const query = useLibrarySelectionInfinite({
    keyword: debounced || undefined,
    kind,
    tagIds: effectiveTag ? [effectiveTag] : undefined,
    pageSize: 24,
  });

  const kindLabel = CATEGORIES.find((item) => item.value === kind)?.label ?? '素材';
  const allowedLabels = CATEGORIES.filter((item) => kinds.includes(item.value))
    .map((item) => item.label)
    .join(' / ');

  function pickKind(next: LibraryAssetKind) {
    setKind(next);
    // 类别换了，标签计数口径也变了；留着上一个类别的标签会筛出空列表
    setTopTagId('');
    setChildTagId('');
  }

  function pickTop(id: string) {
    if (topTagId === id) {
      setTopTagId('');
      setChildTagId('');
      return;
    }
    setTopTagId(id);
    setChildTagId('');
  }

  function pickChild(id: string) {
    // 只动二级：一级要不要一起清，取决于用户是不是先点了一级。
    // 从「全部二级」里直接点进某一组时，取消二级应该回到无筛选，
    // 而不是凭空变成「整组筛选」——那是用户没点过的条件。
    setChildTagId((current) => (current === id ? '' : id));
  }

  function clearTags() {
    setTopTagId('');
    setChildTagId('');
  }

  const items = useMemo(
    () =>
      (query.data?.pages ?? [])
        .flatMap((page) => page.items ?? [])
        // 服务端已按 kind 过滤；这里按「当前类别」再卡一次，防 mock / 旧缓存混进别的类型
        .filter((item) => item.kind === kind),
    [query.data, kind],
  );
  // total 每页都带同一个值：取最后一页的即可，不用另开一个 count 接口
  const total = query.data?.pages.at(-1)?.total ?? 0;
  const full = pickedIds.length >= max;
  /** 当前类别能不能真的挑走：参考图只收 png/jpeg/webp，别的类别只能看 */
  const canPickKind = kinds.includes(kind);

  return (
    <aside
      aria-label="素材库"
      className={cn(
        'fixed right-3 top-[84px] bottom-3 z-[60] flex w-[318px] max-w-[calc(100vw-24px)] flex-col overflow-hidden',
        'rounded-card border border-line-soft bg-panel shadow-pop animate-popIn',
      )}
    >
      <div className="flex items-center gap-1.5 border-b border-line-soft px-3 py-2.5">
        <Library size={14} className="shrink-0 text-brand" />
        <span className="text-[12.5px] font-semibold text-ink">素材库</span>
        <Chip tone={full ? 'warn' : pickedIds.length ? 'primary' : 'default'}>
          {pickedIds.length}/{max}
        </Chip>
        <button
          type="button"
          aria-label="关闭素材库"
          onClick={onClose}
          className="ml-auto grid size-[24px] place-items-center rounded-lg text-ink-mute transition-colors hover:bg-panel-soft hover:text-ink"
        >
          <X size={14} />
        </button>
      </div>

      <div className="space-y-2 border-b border-line-soft px-3 py-2.5">
        <SearchInput
          value={keyword}
          onChange={(event) => setKeyword(event.target.value)}
          placeholder="搜索素材名称…"
          aria-label="搜索素材库"
        />
        {/* 四个类别都能点开看库；接收方不认的类别只浏览，卡片上不给「＋」也不可拖 */}
        <div
          role="group"
          aria-label="素材类别"
          className="flex items-center gap-1 overflow-x-auto pb-0.5"
        >
          {CATEGORIES.map((item) => {
            const active = item.value === kind;
            const allowed = kinds.includes(item.value);
            return (
              <button
                key={item.value}
                type="button"
                aria-pressed={active}
                onClick={() => pickKind(item.value)}
                title={allowed ? `只看${item.label}` : `${item.label}只能浏览：本次只收 ${allowedLabels}`}
                className={cn(
                  'shrink-0 rounded-full border px-2 py-[3px] text-[10.5px] transition',
                  active && 'border-primary-line bg-primary-soft font-semibold text-primary-deep',
                  !active &&
                    'border-line-soft bg-panel-soft text-ink-soft hover:border-line-strong',
                  // 只能看的类别不抢注意力，但仍是可点的正常控件
                  !active && !allowed && 'border-dashed text-ink-mute',
                )}
              >
                {item.label}
              </button>
            );
          })}
        </div>

        {!canPickKind && (
          <p className="rounded-md bg-panel-soft px-2 py-1 text-[10.5px] leading-snug text-ink-mute">
            本次只收 {allowedLabels}：{kindLabel}素材可以在这里浏览，不能加入。
          </p>
        )}

        {/* 两级标签都做成单选横滑：抽屉太窄，多选标签栏会挤成三行、把素材全顶出视野 */}
        <FlyoutTagRow
          label="一级"
          ariaLabel="一级标签"
          allLabel="全部标签"
          allActive={!effectiveTag}
          onAll={clearTags}
          items={top}
          activeId={activeParent}
          onPick={pickTop}
        />

        {/*
          二级行**不随数据消失**：点了一个还没挂系列的一级就整行收起，
          看起来又变回「只有一级」，还让下面一整块内容往上跳。
          词表里一个二级都没有时才退化成一句说明。
        */}
        {hasAnyChild ? (
          <FlyoutTagRow
            label={activeParent ? (byId.get(activeParent)?.name ?? '二级') : '二级'}
            ariaLabel="二级标签"
            allLabel="全部二级标签"
            allActive={!childTagId}
            onAll={() => setChildTagId('')}
            items={children}
            activeId={childTagId}
            onPick={pickChild}
            showParentName={!activeParent}
            hint={children.length ? undefined : '该一级下还没有二级标签'}
          />
        ) : (
          <p className="pl-8 text-[10.5px] leading-snug text-ink-mute">
            还没有二级标签：在资产库「管理标签」里把系列挂到一级下面即可。
          </p>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-2.5">
        {query.isPending ? (
          <div className="grid grid-cols-2 gap-2">
            {Array.from({ length: 6 }).map((_, index) => (
              <Skeleton key={index} className="h-[104px]" />
            ))}
          </div>
        ) : query.isError ? (
          <div className="rounded-lg bg-panel-soft px-3 py-8 text-center text-[11.5px] text-ink-mute">
            素材库加载失败
            <Button
              size="sm"
              variant="ghost"
              className="mt-1.5"
              onClick={() => void query.refetch()}
            >
              重试
            </Button>
          </div>
        ) : items.length ? (
          <div className="grid grid-cols-2 gap-2">
            {items.map((asset) => {
              const order = pickedIds.indexOf(asset.id);
              const picked = order >= 0;
              /* 只能浏览的类别（如参考图里的成片/音频/文本）不给「＋」也不可拖：
                 能拖走再被接收方拒掉，比不给入口更让人困惑 */
              const pickable = kinds.includes(asset.kind);
              const blocked = !pickable || full || picked;
              const payload = dragPayloadOf(asset);
              return (
                <div
                  key={asset.id}
                  // 已选 / 已满 / 不可挑的卡片不再可拖：拖过去也只会被接收方拒掉，
                  // 让它「看起来能拖」等于制造一次无效操作
                  draggable={!blocked}
                  onDragStart={(event) => {
                    event.dataTransfer.effectAllowed = 'copy';
                    writeLibraryDrag(event.dataTransfer, payload);
                  }}
                  title={asset.name}
                  className={cn(
                    'group relative overflow-hidden rounded-lg border bg-panel p-1 transition',
                    picked
                      ? 'border-primary bg-primary-soft/40'
                      : 'border-line-soft hover:border-line-strong',
                    blocked ? 'cursor-default' : 'cursor-grab active:cursor-grabbing',
                  )}
                >
                  {asset.kind === 'text' ? (
                    /* 文本资产没有缩略图：用摘要画文字卡，避免一张 404 破图 */
                    <div className="flex aspect-[4/3] flex-col overflow-hidden rounded-md bg-panel-soft p-1.5">
                      <span className="line-clamp-4 whitespace-pre-wrap break-words text-[10px] leading-[13px] text-ink-soft">
                        {asset.textExcerpt?.trim() || '（空文本）'}
                      </span>
                    </div>
                  ) : (
                    <CoverImage
                      src={asset.coverUrl ?? undefined}
                      seed={asset.id}
                      className="aspect-[4/3] rounded-md"
                    />
                  )}
                  <div className="mt-1 truncate text-[11px] text-ink">
                    {displayName(asset.name)}
                  </div>
                  <div className="truncate text-[10px] text-ink-mute">
                    {asset.kind === 'text'
                      ? '文本资产'
                      : asset.width && asset.height
                        ? `${asset.width}×${asset.height}`
                        : '图片'}
                  </div>

                  {picked ? (
                    <span
                      aria-label={`已加入第 ${order + 1} 张`}
                      className="absolute right-1.5 top-1.5 grid size-[20px] place-items-center rounded-full bg-primary text-[10px] font-semibold text-white"
                    >
                      {order + 1}
                    </span>
                  ) : full || !pickable ? null : (
                    <button
                      type="button"
                      aria-label={`加入参考图：${displayName(asset.name)}`}
                      onClick={() => onPick(payload)}
                      className={cn(
                        'absolute right-1 top-1 grid size-[20px] place-items-center rounded-full',
                        'bg-black/45 text-white opacity-0 transition group-hover:opacity-100',
                        'focus-visible:opacity-100',
                      )}
                    >
                      <Plus size={12} />
                    </button>
                  )}
                  {picked && (
                    <span className="absolute left-1 top-1 grid size-[16px] place-items-center rounded-full bg-primary/90 text-white">
                      <Check size={10} />
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        ) : (
          <div className="flex flex-col items-center gap-1.5 rounded-lg bg-panel-soft px-3 py-10 text-ink-mute">
            <Library size={20} />
            <span className="text-[11.5px]">
              {debounced || effectiveTag
                ? `没有匹配的${kindLabel}素材`
                : `素材库里还没有${kindLabel}素材`}
            </span>
          </div>
        )}

        {query.hasNextPage && (
          <Button
            size="sm"
            variant="ghost"
            block
            className="mt-2"
            loading={query.isFetchingNextPage}
            onClick={() => void query.fetchNextPage()}
          >
            加载更多（{items.length}/{total}）
          </Button>
        )}
      </div>

      <div className="shrink-0 border-t border-line-soft px-3 py-2 text-[10.5px] leading-snug text-ink-mute">
        {full ? (
          <span className="text-warn-deep">已达上限 {max} 张，移除一张后可继续添加</span>
        ) : !canPickKind ? (
          `只能浏览${kindLabel}素材：本次只收 ${allowedLabels}，切到那些类别才能加入。`
        ) : kinds.includes('text') ? (
          '拖文本卡到左侧，或点卡片右上角 ＋ 加入。只显示所选类型，顺序即提交顺序。'
        ) : (
          `拖缩略图到左侧参考图框，或点卡片右上角 ＋ 加入。只显示${kindLabel}素材，顺序即提交顺序。`
        )}
      </div>
    </aside>
  );
}
