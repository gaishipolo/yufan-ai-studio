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

export interface AssetPickerProps {
  open: boolean;
  kind: 'image' | 'video';
  /** 已选的 id：再次点击就是取消选择（多选语义）。 */
  selected: string[];
  onClose: () => void;
  onToggle: (asset: PickedAsset) => void;
}

const PAGE_SIZE = 24;

/**
 * 素材选择器：直接复用素材库的选择接口，不另造一套目录。
 *
 * 只做「挑 id」这一件事——用途、顺序由调用方（素材包/功能）决定，
 * 选择器不该知道提示词编号。
 */
export function AssetPicker({open, kind, selected, onClose, onToggle}: AssetPickerProps) {
  const [keyword, setKeyword] = useState('');
  const params = useMemo(
    () => ({kind, keyword, page: 1, pageSize: PAGE_SIZE, view: 'all'}),
    [kind, keyword],
  );
  const query = useQuery({
    queryKey: ['library-selection', kind, keyword],
    queryFn: () => libraryApi.selection(params),
    enabled: open,
  });
  const items = query.data?.items ?? [];

  return (
    <Modal open={open} onClose={onClose} title={kind === 'video' ? '选择原片' : '选择参考图'} width={880}>
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
            没有可用的{kind === 'video' ? '视频' : '图片'}。先到素材库上传。
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
