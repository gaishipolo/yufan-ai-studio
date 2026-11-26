import { useEffect, useState } from 'react';
import { Download, Layers, RotateCcw, Share2, Tag, Trash2, X } from 'lucide-react';
import { Button, MultiSelect } from '@/shared/ui';
import type { LibraryTagStatOut } from '@/shared/api/schema';
import type { LibraryView } from '@/entities/library/model/types';
import { tagOptions } from '@/entities/library/model/tagTree';

export type BulkKind =
  | 'download'
  | 'delete'
  | 'restore'
  | 'approve'
  | 'reject'
  | 'tag'
  /** 把选中的素材一起共享到同一个目标（团队 / 某个小组） */
  | 'share'
  /** 把选中的素材打成一个封装。不是批量写（不逐条改资产），而是新建一条资产 */
  | 'pack';

export interface LibraryBulkBarProps {
  view: LibraryView;
  count: number;
  tags: LibraryTagStatOut[];
  /** 没有 `library:share` 就不摆这个按钮：与卡片上的单条共享同一口径 */
  canShare?: boolean;
  /** 恢复 / 删除 / 打标签都是写操作：没有 `library:update` 时按钮要禁用，
   *  否则后端 403，用户只看到「点了没反应」。 */
  canUpdate?: boolean;
  busy?: boolean;
  onClear: () => void;
  onAction: (kind: BulkKind, payload?: { tagIds?: string[] }) => void;
}

/**
 * 选中后浮出的批量工具栏。
 *
 * 动作随入口变化（设计 §8），且**没有批量彻底删除**：后端批量写里刻意不含
 * `purge`，不可逆的动作只能逐条确认。回收站入口因此只给「恢复」。
 *
 * **不提供就地新建标签** —— 词表是全局结构，统一在「标签管理」里维护。
 */
export function LibraryBulkBar({
  view,
  count,
  tags,
  canShare = true,
  canUpdate = true,
  busy,
  onClear,
  onAction,
}: LibraryBulkBarProps) {
  const [tagging, setTagging] = useState(false);
  const [tagIds, setTagIds] = useState<string[]>([]);

  /* 选中数归零时复位面板：`count <= 0` 只是把这个组件**藏起来**，它并没有卸载，
     上一批留下的标签会跟着面板一起留在原地——「清空」后再勾一批，
     旧的标签被静默打到新素材上。 */
  useEffect(() => {
    if (count > 0) return;
    setTagging(false);
    setTagIds([]);
  }, [count]);

  if (count <= 0) return null;

  /* 小组 / 团队入口不给删除：卡片层就是按可见范围收掉的（那两个入口里
     素材是共享的，后端也会拒），批量条却照给按钮 → 一点就整批失败。 */
  const canDelete = view !== 'group' && view !== 'team';

  const applyTags = () => {
    onAction('tag', { tagIds });
    setTagging(false);
    setTagIds([]);
  };

  return (
    <div className="sticky bottom-3 z-20 space-y-2 rounded-card border border-primary-line bg-panel px-2.5 py-2 shadow-float">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-body font-semibold text-ink">已选 {count} 项</span>
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
              disabled={busy || !canUpdate}
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
              {canShare && (
                <Button
                  size="sm"
                  variant="secondary"
                  icon={<Share2 size={12} />}
                  disabled={busy}
                  onClick={() => onAction('share')}
                >
                  共享
                </Button>
              )}
              <Button
                size="sm"
                variant="secondary"
                icon={<Layers size={12} />}
                disabled={busy}
                onClick={() => onAction('pack')}
              >
                组建封装
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
              {canDelete && (
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
              )}
            </>
          )}
        </div>
      </div>

      {tagging && (
        <div className="flex items-end gap-2 border-t border-line-soft pt-2">
          <div className="w-full max-w-[320px]">
            <MultiSelect
              options={tagOptions(tags)}
              value={tagIds}
              onChange={setTagIds}
              placeholder="选择要添加的标签"
              emptyText="还没有可用标签"
              ariaLabel="批量添加标签"
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
