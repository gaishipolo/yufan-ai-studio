import { Check, Download, FileAudio, Film, Image as ImageIcon, Package, RotateCcw, Share2, Trash2, Undo2 } from 'lucide-react';
import { Button, Chip, CoverImage } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import { formatBytes, formatDuration } from '@/shared/lib/format';
import type { LibraryAssetOut } from '@/shared/api/schema';
import {
  KIND_LABEL,
  ORIGIN_LABEL,
  deleteOffer,
  isTrashExpired,
  trashDaysLeft,
  type LibraryView,
} from '@/entities/library/model/types';

export type LibraryCardAction =
  | 'open'
  | 'share'
  | 'revoke'
  | 'delete'
  | 'restore'
  | 'purge'
  | 'approve'
  | 'reject'
  | 'download';

export interface LibraryCardProps {
  asset: LibraryAssetOut;
  view: LibraryView;
  selected: boolean;
  viewer: { id: string; canDeleteAny: boolean };
  onToggle: (id: string) => void;
  onAction: (action: LibraryCardAction, asset: LibraryAssetOut) => void;
}

const KIND_ICON = {
  video: Film,
  audio: FileAudio,
  image: ImageIcon,
  pack: Package,
} as const;

/**
 * 资产卡。
 *
 * 操作按钮**按入口变化**，不是按资产能力变化（设计 §8）：小组 / 团队入口
 * 不出现删除按钮，只给「取消共享」，因为在那两个入口删别人的东西本来就不允许；
 * 管理员多出来的「强制移入回收站」必须填理由，判定走 `deleteOffer`。
 */
export function LibraryCard({ asset, view, selected, viewer, onToggle, onAction }: LibraryCardProps) {
  const Icon = KIND_ICON[asset.kind] ?? ImageIcon;
  const offer = deleteOffer(asset, viewer);
  const days = view === 'trash' ? trashDaysLeft(asset.trashExpiresAt) : null;
  const expired = view === 'trash' && isTrashExpired(asset.trashExpiresAt);

  const meta = [
    asset.sizeBytes != null ? formatBytes(asset.sizeBytes) : null,
    asset.durationSec ? formatDuration(asset.durationSec) : null,
  ].filter(Boolean);

  return (
    <article
      className={cn(
        'group relative flex flex-col overflow-hidden rounded-card border bg-panel shadow-card transition-all',
        selected ? 'border-primary ring-2 ring-primary/15' : 'border-line-soft hover:border-line-strong',
        expired && 'opacity-55',
      )}
    >
      <div className="relative">
        <button
          type="button"
          onClick={() => onAction('open', asset)}
          className="block w-full text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
          aria-label={`查看 ${asset.name}`}
        >
          <CoverImage src={asset.coverUrl ?? undefined} seed={asset.id} className="aspect-[4/3]" />
          <div className="pointer-events-none absolute inset-x-0 bottom-0 flex items-end justify-between gap-1 bg-gradient-to-t from-[rgba(12,24,30,.55)] to-transparent px-2 pb-1.5 pt-6">
            <span className="inline-flex items-center gap-1 text-[10.5px] font-medium text-white/95">
              <Icon size={11} />
              {KIND_LABEL[asset.kind]}
            </span>
            {view === 'trash' && days != null && (
              <span className="rounded bg-black/35 px-1 text-[10px] text-white/90">
                {expired ? '已过期' : `剩余 ${days} 天`}
              </span>
            )}
          </div>
        </button>

        <label
          className={cn(
            'absolute left-1.5 top-1.5 grid size-[18px] cursor-pointer place-items-center rounded-[5px] border transition',
            selected
              ? 'border-primary bg-primary text-white'
              : 'border-white/70 bg-white/85 text-transparent opacity-0 group-hover:opacity-100',
          )}
          aria-label={`选择 ${asset.name}`}
        >
          <input
            type="checkbox"
            className="sr-only"
            checked={selected}
            onChange={() => onToggle(asset.id)}
          />
          <Check size={12} />
        </label>

        {asset.visibility !== 'personal' && view !== 'trash' && (
          <span className="absolute right-1.5 top-1.5">
            <Chip tone={asset.visibility === 'team' ? 'primary' : 'ok'}>
              {asset.visibility === 'team' ? '团队' : '小组'}
            </Chip>
          </span>
        )}
      </div>

      <div className="flex min-h-0 flex-1 flex-col gap-1 p-2.5">
        <button
          type="button"
          onClick={() => onAction('open', asset)}
          className="truncate text-left text-[12.5px] font-medium text-ink hover:text-primary-deep"
          title={asset.name}
        >
          {asset.name}
        </button>
        <p className="truncate text-[11px] text-ink-mute">
          {[ORIGIN_LABEL[asset.origin] ?? asset.origin, asset.ownerName || null, ...meta]
            .filter(Boolean)
            .join(' · ')}
        </p>

        <div className="mt-auto flex flex-wrap items-center gap-1 pt-1">
          {view === 'drafts' ? (
            <>
              <Button size="sm" variant="primary" onClick={() => onAction('approve', asset)}>
                入库
              </Button>
              <Button size="sm" variant="ghost" onClick={() => onAction('reject', asset)}>
                驳回
              </Button>
            </>
          ) : null}

          {view === 'trash' ? (
            <>
              <Button
                size="sm"
                variant="secondary"
                icon={<RotateCcw size={12} />}
                onClick={() => onAction('restore', asset)}
              >
                恢复
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="text-danger hover:text-danger-deep"
                icon={<Trash2 size={12} />}
                onClick={() => onAction('purge', asset)}
              >
                彻底删除
              </Button>
            </>
          ) : null}

          {view !== 'drafts' && view !== 'trash' && offer === 'revoke' && (
            <Button
              size="sm"
              variant="ghost"
              icon={<Undo2 size={12} />}
              title="取消共享需要审批通过后才能删除"
              onClick={() => onAction('revoke', asset)}
            >
              取消共享
            </Button>
          )}

          {view !== 'drafts' && view !== 'trash' && offer !== 'revoke' && (
            <Button
              size="sm"
              variant="ghost"
              icon={<Share2 size={12} />}
              onClick={() => onAction('share', asset)}
            >
              {asset.visibility === 'personal' ? '共享到…' : '改共享'}
            </Button>
          )}

          {view !== 'drafts' && view !== 'trash' && offer === 'trash' && (
            <Button
              size="sm"
              variant="ghost"
              className="text-danger hover:text-danger-deep"
              icon={<Trash2 size={12} />}
              onClick={() => onAction('delete', asset)}
            >
              移入回收站
            </Button>
          )}

          {view !== 'drafts' && view !== 'trash' && offer === 'force' && (
            <Button
              size="sm"
              variant="ghost"
              className="text-danger hover:text-danger-deep"
              icon={<Trash2 size={12} />}
              onClick={() => onAction('delete', asset)}
            >
              强制移入
            </Button>
          )}

          {view !== 'drafts' && view !== 'trash' && asset.state === 'active' && (
            <Button
              size="sm"
              variant="ghost"
              icon={<Download size={12} />}
              onClick={() => onAction('download', asset)}
            >
              下载
            </Button>
          )}
        </div>
      </div>

      {asset.state === 'draft' && (
        <span className="absolute right-1.5 top-1.5">
          <Chip tone="warn">草稿</Chip>
        </span>
      )}
      {view === 'trash' && (
        <span className="absolute right-1.5 top-1.5">
          <Chip tone="danger">回收站</Chip>
        </span>
      )}
    </article>
  );
}
