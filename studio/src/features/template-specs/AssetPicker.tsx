import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { libraryApi } from '@/entities/library/api/library.api';
import { CoverImage, Modal, SearchInput } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';

export interface PickedAsset {
  id: string;
  name: string;
  coverUrl?: string;
}

/** 选择器支持的素材类型。`pack` 是资产库封装（多成员、带角色与顺序）。 */
export type PickKind = 'image' | 'video' | 'audio' | 'text' | 'pack';

export interface AssetPickerProps {
  open: boolean;
  kind: PickKind;
  /**
   * 候选集收窄到这些标签（含其下级）。空 = 不限。
   *
   * 槽位的一级标签就是从这里传进来的：模板作者挑固定素材、模板使用者填表，
   * 看到的是**同一批**素材 —— 两处各筛各的，就会出现「作者选的图用户根本挑不到」。
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
  pack: '选择封装',
};

const KIND_LABELS: Record<PickKind, string> = {
  image: '图片',
  video: '视频',
  audio: '音频',
  text: '文本',
  pack: '封装',
};

/**
 * 素材选择器：直接复用素材库的选择接口，不另造一套目录。
 *
 * 只做「挑 id」这一件事——用途、顺序由调用方（素材包/功能）决定，
 * 选择器不该知道提示词编号。
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
  const scope = tagIds ?? [];
  const params = useMemo(
    () => ({kind, keyword, page: 1, pageSize: PAGE_SIZE, view: 'all', tagIds: scope}),
    // `scope` 是每次 render 新建的数组，用它当依赖会无限重取；用 join 后的串。
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [kind, keyword, scope.join(',')],
  );
  const query = useQuery({
    queryKey: ['library-selection', kind, keyword, scope.join(',')],
    queryFn: () => libraryApi.selection(params),
    enabled: open,
  });
  const items = query.data?.items ?? [];

  return (
    <Modal open={open} onClose={onClose} title={TITLES[kind]} width={880}>
      <div className="flex flex-col gap-3">
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
                  onClick={() => onToggle({id: item.id, name: item.name, coverUrl: item.coverUrl ?? undefined})}
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
                  <span className="line-clamp-1 px-0.5 text-[11px] text-ink-soft">{item.name}</span>
                  <span className={cn('px-0.5 text-[10px]', active ? 'text-primary' : 'text-ink-mute')}>
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
