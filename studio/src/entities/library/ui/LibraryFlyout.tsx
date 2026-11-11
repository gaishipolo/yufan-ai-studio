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
 */
import { useMemo, useState } from 'react';
import { Check, Library, Plus, X } from 'lucide-react';
import { Button, Chip, CoverImage, SearchInput, Skeleton } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import { useDebouncedValue } from '@/shared/hooks/useDebouncedValue';
import type { LibraryAssetOut } from '@/shared/api/schema';
import { useLibrarySelectionInfinite, useLibraryTags } from '../api/library.queries';
import { displayName } from '../model/types';
import { writeLibraryDrag, type LibraryAssetDrag } from '@/shared/lib/libraryDrag';

export interface LibraryFlyoutProps {
  onClose: () => void;
  /** 接收方声明的上限（模型的 maxReferences） */
  max: number;
  /** 已经在参考图列表里的素材 id，顺序即最终生效顺序 */
  pickedIds: string[];
  /** 点击「＋」加入。拖拽走 `drag.ts` 的载荷，不经过这里 */
  onPick: (asset: LibraryAssetDrag) => void;
}

/** 缩略图优先封面；没有封面就退回预览地址，再没有就交给 CoverImage 画占位 */
function dragPayloadOf(asset: LibraryAssetOut): LibraryAssetDrag {
  return {
    id: asset.id,
    name: asset.name,
    url: asset.coverUrl ?? asset.previewUrl ?? asset.url ?? '',
  };
}

export function LibraryFlyout({ onClose, max, pickedIds, onPick }: LibraryFlyoutProps) {
  const [keyword, setKeyword] = useState('');
  const [tagId, setTagId] = useState('');
  const debounced = useDebouncedValue(keyword, 250);

  // 标签计数按入口变化；这里固定「全部素材」口径，与选择器的可见集一致
  const tagScope = useMemo(() => ({ state: 'active' as const, library: 'all' as const }), []);

  const query = useLibrarySelectionInfinite({
    keyword: debounced || undefined,
    kind: 'image',
    tagIds: tagId ? [tagId] : undefined,
    pageSize: 24,
  });
  const tags = useLibraryTags(tagScope);

  const items = useMemo(
    () => (query.data?.pages ?? []).flatMap((page) => page.items ?? []),
    [query.data],
  );
  // total 每页都带同一个值：取最后一页的即可，不用另开一个 count 接口
  const total = query.data?.pages.at(-1)?.total ?? 0;
  const full = pickedIds.length >= max;

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
        {/* 标签做成单选横滑：抽屉太窄，多选标签栏会挤成三行、把素材全顶出视野 */}
        <div className="flex gap-1 overflow-x-auto pb-0.5">
          <button
            type="button"
            onClick={() => setTagId('')}
            aria-pressed={!tagId}
            className={cn(
              'shrink-0 rounded-full border px-2 py-[3px] text-[10.5px] transition',
              !tagId
                ? 'border-primary-line bg-primary-soft text-primary-deep'
                : 'border-line-soft bg-panel-soft text-ink-soft hover:border-line-strong',
            )}
          >
            全部标签
          </button>
          {(tags.data ?? []).map((tag) => (
            <button
              key={tag.id}
              type="button"
              onClick={() => setTagId((current) => (current === tag.id ? '' : tag.id))}
              aria-pressed={tagId === tag.id}
              className={cn(
                'shrink-0 rounded-full border px-2 py-[3px] text-[10.5px] transition',
                tagId === tag.id
                  ? 'border-primary-line bg-primary-soft text-primary-deep'
                  : 'border-line-soft bg-panel-soft text-ink-soft hover:border-line-strong',
              )}
            >
              {tag.name}
              <span className="ml-1 text-ink-mute">{tag.assetCount ?? 0}</span>
            </button>
          ))}
        </div>
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
              const blocked = full || picked;
              const payload = dragPayloadOf(asset);
              return (
                <div
                  key={asset.id}
                  // 已选 / 已满的卡片不再可拖：拖过去也只会被接收方拒掉，
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
                  <CoverImage
                    src={asset.coverUrl ?? undefined}
                    seed={asset.id}
                    className="aspect-[4/3] rounded-md"
                  />
                  <div className="mt-1 truncate text-[11px] text-ink">
                    {displayName(asset.name)}
                  </div>
                  <div className="truncate text-[10px] text-ink-mute">
                    {asset.width && asset.height ? `${asset.width}×${asset.height}` : '图片'}
                  </div>

                  {picked ? (
                    <span
                      aria-label={`已加入第 ${order + 1} 张`}
                      className="absolute right-1.5 top-1.5 grid size-[20px] place-items-center rounded-full bg-primary text-[10px] font-semibold text-white"
                    >
                      {order + 1}
                    </span>
                  ) : full ? null : (
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
              {debounced || tagId ? '没有匹配的图片素材' : '素材库里还没有图片素材'}
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
        ) : (
          '拖缩略图到左侧参考图框，或点卡片右上角 ＋ 加入。只显示图片素材，顺序即提交顺序。'
        )}
      </div>
    </aside>
  );
}
