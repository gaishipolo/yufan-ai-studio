import { useState } from 'react';
import { Download, PackagePlus, RotateCcw, Tag, Trash2, X } from 'lucide-react';
import { Button, MultiSelect } from '@/shared/ui';
import type { LibraryTagOut } from '@/shared/api/schema';
import type { LibraryView } from '@/entities/library/model/types';
import { useCreateTagInline } from './use-create-tag-inline';

export type BulkKind = 'download' | 'pack' | 'delete' | 'restore' | 'approve' | 'reject' | 'tag';

export interface LibraryBulkBarProps {
  view: LibraryView;
  count: number;
  tags: LibraryTagOut[];
  busy?: boolean;
  onClear: () => void;
  onAction: (kind: BulkKind, payload?: { tagIds?: string[] }) => void;
}

/**
 * 选中后浮出的批量工具栏。
 *
 * 动作随入口变化（设计 §8），且**没有批量彻底删除**：后端批量写里刻意不含
 * `purge`，不可逆的动作只能逐条确认。回收站入口因此只给「恢复」。
 */
export function LibraryBulkBar({ view, count, tags, busy, onClear, onAction }: LibraryBulkBarProps) {
  const [tagging, setTagging] = useState(false);
  const [tagIds, setTagIds] = useState<string[]>([]);
  const createTag = useCreateTagInline();

  if (count <= 0) return null;

  const applyTags = () => {
    onAction('tag', { tagIds });
    setTagging(false);
    setTagIds([]);
  };

  return (
    <div className="sticky bottom-3 z-20 space-y-2 rounded-card border border-primary-line bg-white px-2.5 py-2 shadow-float">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-[12px] font-semibold text-ink">已选 {count} 项</span>
        <Button size="sm" variant="ghost" icon={<X size={12} />} onClick={onClear}>
          清空
        </Button>

        <div className="ml-auto flex flex-wrap items-center gap-1.5">
          {view === 'drafts' ? (
            <>
              <Button size="sm" variant="primary" disabled={busy} onClick={() => onAction('approve')}>
                批量入库
              </Button>
              <Button size="sm" variant="ghost" disabled={busy} onClick={() => onAction('reject')}>
                批量驳回
              </Button>
            </>
          ) : view === 'trash' ? (
            <Button
              size="sm"
              variant="secondary"
              icon={<RotateCcw size={12} />}
              disabled={busy}
              onClick={() => onAction('restore')}
            >
              批量恢复
            </Button>
          ) : (
            <>
              <Button
                size="sm"
                variant="secondary"
                icon={<Download size={12} />}
                disabled={busy}
                onClick={() => onAction('download')}
              >
                打包下载
              </Button>
              <Button
                size="sm"
                variant="secondary"
                icon={<Tag size={12} />}
                disabled={busy}
                onClick={() => setTagging((value) => !value)}
              >
                打标签
              </Button>
              <Button
                size="sm"
                variant="secondary"
                icon={<PackagePlus size={12} />}
                disabled={busy}
                onClick={() => onAction('pack')}
              >
                封装
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="text-danger hover:text-danger-deep"
                icon={<Trash2 size={12} />}
                disabled={busy}
                onClick={() => onAction('delete')}
              >
                移入回收站
              </Button>
            </>
          )}
        </div>
      </div>

      {tagging && (
        <div className="flex items-end gap-2 border-t border-line-soft pt-2">
          <div className="w-full max-w-[320px]">
            <MultiSelect
              options={tags.map((tag) => ({ value: tag.id, label: tag.name }))}
              value={tagIds}
              onChange={setTagIds}
              placeholder="选择要添加的标签"
              emptyText="还没有标签"
              ariaLabel="批量添加标签"
              onCreate={createTag}
            />
          </div>
          <Button size="sm" variant="primary" disabled={!tagIds.length || busy} onClick={applyTags}>
            应用到 {count} 项
          </Button>
        </div>
      )}
    </div>
  );
}
