import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Check } from 'lucide-react';
import { useCan } from '@/shared/access/use-access';
import { libraryApi } from '@/entities/library/api/library.api';
import { useLibraryTags } from '@/entities/library/api/library.queries';
import { scopeFilterIds } from '@/entities/library/model/tagTree';
import { viewQuery } from '@/entities/library/model/types';
import { TagFilter } from '@/entities/library/ui/TagFilter';
import { CoverImage, Modal, SearchInput } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';

export interface PickedAsset {
  id: string;
  name: string;
  coverUrl?: string;
  /** 资产用途：`case` 表示已经在案例池里。模板案例要据此决定是否弹二次确认。 */
  purpose?: string;
}

/** 选择器支持的素材类型。 */
export type PickKind = 'image' | 'video' | 'audio' | 'text';

export interface AssetPickerProps {
  open: boolean;
  kind: PickKind;
  /**
   * 候选集收窄到这些标签（含其下级）。空 = 不限。
   *
   * 槽位的标签就是从这里传进来的：模板作者挑固定素材、模板使用者填表，
   * 看到的是**同一批**素材 —— 两处各筛各的，就会出现「作者选的图用户根本挑不到」。
   *
   * 标签可以是任意级别（分组 / 系列 / 具体词），多个标签之间是并集。
   */
  tagIds?: string[];
  /** 已选的 id：再次点击就是取消选择（多选语义）。 */
  selected: string[];
  /**
   * 允许在「我的素材」与「案例池」之间切。
   *
   * 只有设置**展示案例**时要开：案例不进普通列表，选择器默认也把它排掉，
   * 所以作者传过的案例第二次根本挑不到（只能用一次）。切到案例池的那一档
   * 只列 `purpose=case` 的素材（服务端要 `library:case:read`）。
   */
  casePool?: boolean;
  /** 标题。默认按类型给（「选择参考图」）；设置展示案例时要换成「选择展示案例」。 */
  title?: string;
  onClose: () => void;
  onToggle: (asset: PickedAsset) => void;
}

const PAGE_SIZE = 24;

const TITLES: Record<PickKind, string> = {
  image: '选择参考图',
  video: '选择视频',
  audio: '选择音频',
  text: '选择文本',
};

const KIND_LABELS: Record<PickKind, string> = {
  image: '图片',
  video: '视频',
  audio: '音频',
  text: '文本',
};

/**
 * 素材选择器：直接复用素材库的选择接口，不另造一套目录。
 *
 * 只做「挑 id」这一件事——用途、顺序由调用方（素材包/功能）决定，
 * 选择器不该知道提示词编号。
 *
 * 标签筛选是**三级 + 多选**：候选集（作者的槽位标签）是范围，用户还能在
 * 范围里再挑更细的标签；挑多个是并集。
 */
export function AssetPicker({
  open,
  kind,
  tagIds,
  selected,
  casePool,
  title,
  onClose,
  onToggle,
}: AssetPickerProps) {
  const [keyword, setKeyword] = useState('');
  /** 用户在候选集里又挑的标签（可空）。 */
  const [picked, setPicked] = useState<string[]>([]);
  /** 看「我的素材」还是「案例池」。只有 `casePool` 打开时才出这个开关。 */
  const [pool, setPool] = useState<'mine' | 'case'>('mine');
  /* 案例池要么看得到（`library:case:read`），要么这一档根本不出现 ——
     后端对 `purpose=case` 直接 403，摆出来就是让人点一次错。 */
  const canReadCases = useCan('library:case:read');
  /* 候选集（作者的槽位标签）：**引用要稳定** —— 它是下面 filterIds 这条
     useMemo 的依赖，每次 render 新建一个数组会让那个 memo 每次都重算。 */
  const scope = useMemo(() => tagIds ?? [], [tagIds]);
  const scopeKey = scope.join(',');
  /*
   * 标签词表按**候选范围**收窄：只统计符合候选集的那批素材。
   * 界面上能点什么，取决于这批素材身上实际有什么 —— 按全库拿会列出一堆
   * 这批素材一个都没挂的标签，用户点了只得到空列表。
   */
  const tags = useLibraryTags({
    ...viewQuery('all'),
    ...(scope.length ? { tagIds: scope } : {}),
  });

  /* 换了一个槽位 = 换了候选集，之前挑的标签可能已经在范围外：清掉重来。 */
  useEffect(() => {
    setPicked([]);
  }, [scopeKey]);

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
  const filterKey = filterIds.join(',');

  const params = useMemo(
    () => ({
      kind,
      keyword,
      page: 1,
      pageSize: PAGE_SIZE,
      tagIds: filterIds,
      /* 案例池是**另一个口径**，不是「再加一个筛选」：不传就排除案例。 */
      ...(pool === 'case' ? {purpose: 'case'} : {}),
    }),
    // `filterIds` 是每次 render 新建的数组，用它当依赖会无限重取；用 join 后的串。
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [kind, keyword, filterKey, pool],
  );
  const query = useQuery({
    queryKey: ['library-selection', kind, keyword, filterKey, pool],
    queryFn: () => libraryApi.selection(params),
    enabled: open,
  });
  const items = query.data?.items ?? [];

  /* 每次打开都从「我的素材」开始：上一次停在案例池，下一次挑槽位参考图时
     会一进来就是空的（案例池里没有参考图），看起来像库里没东西。 */
  useEffect(() => {
    if (open) setPool('mine');
  }, [open]);

  return (
    <Modal open={open} onClose={onClose} title={title ?? TITLES[kind]} width={880}>
      <div className="flex flex-col gap-3">
        {/* 「我的素材 / 案例池」两档。只在设置展示案例时出现：其他入口
            （槽位挑参考图）没有案例池这回事，摆一个永远灰的档只会让人问
            「案例为什么不在这里」。 */}
        {casePool && canReadCases && (
          <div className="inline-flex w-fit items-center gap-0.5 rounded-xl bg-canvas/90 p-0.5 ring-1 ring-inset ring-[rgba(203,218,224,.8)]">
            {(
              [
                ['mine', '我的素材'],
                ['case', '案例池'],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                type="button"
                aria-pressed={pool === value}
                onClick={() => setPool(value)}
                className={cn(
                  'rounded-[10px] px-3 py-[5px] text-meta transition-all duration-150',
                  pool === value
                    ? 'bg-panel font-medium text-ink shadow-[0_1px_3px_rgba(21,50,61,.12)]'
                    : 'text-ink-mute hover:text-ink-soft',
                )}
              >
                {label}
              </button>
            ))}
          </div>
        )}
        {/* 案例池里不摆标签行：标签计数是**整个库**的口径（标签接口没有
            `purpose`），摆出来会列一堆池子里一张都没有的标签，点下去只得到
            空列表。案例池本来就只有几十条，按名字搜更快。 */}
        {pool === 'mine' && (
        <TagFilter
          tags={tags.data ?? []}
          onlyWithAssets
          hideTopLevel
          excludeIds={scope}
          single
          value={picked}
          onChange={setPicked}
        />
        )}
        <SearchInput
          value={keyword}
          onChange={event => setKeyword(event.target.value)}
          placeholder="按名称搜索"
        />
        {query.isLoading ? (
          <p className="py-10 text-center text-xs text-ink-mute">正在读取素材…</p>
        ) : items.length === 0 ? (
          <p className="py-10 text-center text-xs text-ink-mute">
            {pool === 'case'
              ? `案例池里还没有${KIND_LABELS[kind]}，换个「我的素材」挑一条转成案例。`
              : `没有可用的${KIND_LABELS[kind]}。先到素材库上传。`}
          </p>
        ) : (
          <div className="grid max-h-[420px] grid-cols-3 gap-3 overflow-y-auto pr-1 sm:grid-cols-4">
            {items.map(item => {
              const active = selected.includes(item.id);
              return (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => onToggle({
                    id: item.id,
                    name: item.name,
                    coverUrl: item.coverUrl ?? undefined,
                    purpose: item.purpose,
                  })}
                  className={cn(
                    'group relative flex flex-col gap-1 rounded-xl border p-1.5 text-left transition-colors',
                    active
                      ? 'border-primary-line bg-primary/5'
                      : 'border-line hover:border-line-strong',
                  )}
                >
                  {/* 文本资产没有缩略图：后端对 `kind=text` 的 `/thumbnail`
                      直接 404（「文本资产没有缩略图」）。用摘要画一张文字卡，
                      别让每个文本格子都发一次注定失败的请求。

                      素材库里另外三处（`LibraryCard` / `LibraryFlyout` /
                      `LibraryPicker`）早就这么画了 —— 这里原本是四份渲染
                      里唯一漏掉的一处：所有类型一律画 `CoverImage`，
                      于是选文本时满屏都是渐变占位，看不出选的是哪段文字。 */}
                  {item.kind === 'text' ? (
                    <div className="flex aspect-video w-full flex-col overflow-hidden rounded-lg bg-panel-soft p-2">
                      <span className="line-clamp-4 whitespace-pre-wrap break-words text-micro leading-[13px] text-ink-soft">
                        {item.textExcerpt?.trim() || '（空文本）'}
                      </span>
                    </div>
                  ) : (
                    <CoverImage
                      src={item.coverUrl ?? undefined}
                      seed={item.id}
                      alt={item.name}
                      className="aspect-video w-full rounded-lg"
                    />
                  )}
                  <span className="line-clamp-1 px-0.5 text-meta text-ink-soft">{item.name}</span>
                  {/* 选中态用**勾号图标**，不写「已选（点击取消）」/「点击选择」。
                      原来每个格子都念一遍同一句操作提示（24 格 = 24 遍），
                      而整个网格就是可点的 —— 勾号是同一个意思、更省地方，
                      与 `SlotMediaPicker` 的网格也统一了。 */}
                  <span
                    className={cn(
                      'absolute right-1 top-1 grid size-4 place-items-center rounded-full',
                      active ? 'bg-primary text-white' : 'bg-black/45 text-transparent',
                    )}
                  >
                    <Check size={10} strokeWidth={3} />
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </div>
    </Modal>
  );
}
