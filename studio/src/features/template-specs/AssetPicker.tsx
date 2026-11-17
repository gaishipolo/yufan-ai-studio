import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
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
  onClose,
  onToggle,
}: AssetPickerProps) {
  const [keyword, setKeyword] = useState('');
  /** 用户在候选集里又挑的标签（可空）。 */
  const [picked, setPicked] = useState<string[]>([]);
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
    }),
    // `filterIds` 是每次 render 新建的数组，用它当依赖会无限重取；用 join 后的串。
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [kind, keyword, filterKey],
  );
  const query = useQuery({
    queryKey: ['library-selection', kind, keyword, filterKey],
    queryFn: () => libraryApi.selection(params),
    enabled: open,
  });
  const items = query.data?.items ?? [];

  return (
    <Modal open={open} onClose={onClose} title={TITLES[kind]} width={880}>
      <div className="flex flex-col gap-3">
        <TagFilter
          tags={tags.data ?? []}
          onlyWithAssets
          hideTopLevel
          excludeIds={scope}
          single
          value={picked}
          onChange={setPicked}
        />
        <SearchInput
          value={keyword}
          onChange={event => setKeyword(event.target.value)}
          placeholder="按名称搜索"
        />
        {query.isLoading ? (
          <p className="py-10 text-center text-xs text-ink-mute">正在读取素材…</p>
        ) : items.length === 0 ? (
          <p className="py-10 text-center text-xs text-ink-mute">
            没有可用的{KIND_LABELS[kind]}。先到素材库上传。
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
                    'group flex flex-col gap-1 rounded-xl border p-1.5 text-left transition-colors',
                    active
                      ? 'border-primary-line bg-primary/5'
                      : 'border-line hover:border-line-strong',
                  )}
                >
                  <CoverImage
                    src={item.coverUrl ?? undefined}
                    seed={item.id}
                    alt={item.name}
                    className="aspect-video w-full rounded-lg"
                  />
                  <span className="line-clamp-1 px-0.5 text-meta text-ink-soft">{item.name}</span>
                  <span className={cn('px-0.5 text-micro', active ? 'text-primary' : 'text-ink-mute')}>
                    {active ? '已选（点击取消）' : '点击选择'}
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
