import { Check, Download, FileAudio, Film, Image as ImageIcon, Package, Plus, RotateCcw, Share2, Trash2, Undo2, X } from 'lucide-react';
import { Button, Chip, CoverImage, IconButton } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';
import { formatBytes, formatDuration } from '@/shared/lib/format';
import type { LibraryAssetOut } from '@/shared/api/schema';
import {
  KIND_LABEL,
  ORIGIN_LABEL,
  deleteOffer,
  displayName,
  isTrashExpired,
  trashDaysLeft,
  type LibraryView,
} from '@/entities/library/model/types';
import { tagLabel } from '@/entities/library/model/tagTree';

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
  /** 后端 PATCH 要 `library:update`，且只认本人 / `delete:any`；界面先少显示 × */
  canUpdate: boolean;
  /** 正在提交去标签时禁用所有 ×，避免连点发出几次互相覆盖的 PATCH */
  tagBusy?: boolean;
  onToggle: (id: string) => void;
  onAction: (action: LibraryCardAction, asset: LibraryAssetOut) => void;
  onRemoveTag: (asset: LibraryAssetOut, tagId: string) => void;
  onAddTag: (asset: LibraryAssetOut) => void;
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
export function LibraryCard({
  asset,
  view,
  selected,
  viewer,
  canUpdate,
  tagBusy,
  onToggle,
  onAction,
  onRemoveTag,
  onAddTag,
}: LibraryCardProps) {
  const Icon = KIND_ICON[asset.kind] ?? ImageIcon;
  const canRemoveTags =
    canUpdate && (asset.ownerId === viewer.id || viewer.canDeleteAny);
  const offer = deleteOffer(asset, viewer);
  const days = view === 'trash' ? trashDaysLeft(asset.trashExpiresAt) : null;
  const expired = view === 'trash' && isTrashExpired(asset.trashExpiresAt);
  /* 卡片上不带格式后缀：后缀由文件决定、用户改不了，摆在名字里只会让人以为
     能编辑（`displayName` 的注释里有完整口径）。`title` 仍给完整文件名，
     鼠标停一下能看到真实文件叫什么。 */
  const title = displayName(asset.name);
  const tags = asset.tags ?? [];

  /* 元信息拆两行：来源与作者同一行、体积与时长另一行。并成一行时 196px 的
     列宽只放得下前两项，`truncate` 会把体积时长直接截掉——而体积恰恰是用户
     在「挑一条素材去用」时最先比较的东西。 */
  const context = [ORIGIN_LABEL[asset.origin] ?? asset.origin, asset.ownerName || null].filter(
    Boolean,
  );
  const facts = [
    asset.sizeBytes != null ? formatBytes(asset.sizeBytes) : null,
    asset.durationSec ? formatDuration(asset.durationSec) : null,
  ].filter(Boolean);

  return (
    <article
      className={cn(
        'group relative flex flex-col overflow-hidden rounded-card border bg-panel shadow-card',
        'transition-[box-shadow,border-color,transform] duration-200 ease-smooth',
        selected
          ? 'border-primary bg-primary-soft/25 ring-2 ring-primary/30'
          : 'border-line-soft hover:-translate-y-0.5 hover:border-line-strong hover:shadow-float',
        expired && 'opacity-55',
      )}
    >
      <div className="relative">
        <button
          type="button"
          onClick={() => onAction('open', asset)}
          className="block w-full text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
          aria-label={`查看 ${title}`}
        >
          <CoverImage
            src={asset.coverUrl ?? undefined}
            seed={asset.id}
            className="aspect-[4/3]"
            imgClassName="transition-transform duration-300 ease-smooth group-hover:scale-[1.03]"
          />
          {/* 没有封面（音频、抽帧失败的视频）时给一个类型图标：空渐变块在网格里
              看起来像加载失败，用户分不清「没有图」和「图挂了」。 */}
          {!asset.coverUrl && (
            <span className="pointer-events-none absolute inset-0 grid place-items-center">
              <Icon size={30} className="text-white/75" strokeWidth={1.6} />
            </span>
          )}
          <div className="pointer-events-none absolute inset-x-0 bottom-0 flex items-end justify-between gap-1 bg-gradient-to-t from-[rgba(12,24,30,.62)] via-[rgba(12,24,30,.28)] to-transparent px-2 pb-1.5 pt-7">
            <span className="inline-flex items-center gap-1 text-[12.5px] font-medium text-white/95">
              <Icon size={11} />
              {KIND_LABEL[asset.kind]}
            </span>
            {view === 'trash' && days != null && (
              <span className="rounded bg-black/40 px-1 text-[12px] text-white/90">
                {expired ? '已过期' : `剩余 ${days} 天`}
              </span>
            )}
          </div>
        </button>

        {/* 勾选框要在**任意**底图上都看得见。原来未选中是「白底 + 白边 + 只在
            hover 出现」，压在白色素材上整块消失，触屏也没有 hover 可等。
            现在用「白底 + 深描边 + 白色外晕 + 投影」：
            - 白素材上：深描边给出形状，外晕让它在半白区域也分离；
            - 深素材上：白底与外晕给出形状。
            语义也对：空心 = 未选中，不再是「一块深灰方块」（那看起来像已勾）。 */}
        <label
          className={cn(
            'absolute left-1.5 top-1.5 grid size-[20px] cursor-pointer place-items-center rounded-[6px] border transition',
            'focus-within:ring-2 focus-within:ring-primary/40',
            selected
              ? 'border-2 border-primary bg-primary text-white shadow-[0_0_0_1.5px_rgba(255,255,255,.9),0_1px_5px_rgba(9,20,26,.28)]'
              : 'border-2 border-[rgba(12,24,30,.55)] bg-white/95 text-transparent shadow-[0_0_0_1.5px_rgba(255,255,255,.85),0_1px_5px_rgba(9,20,26,.32)] hover:border-[rgba(12,24,30,.72)] hover:bg-white',
          )}
          aria-label={`选择 ${title}`}
        >
          <input
            type="checkbox"
            className="sr-only"
            checked={selected}
            onChange={() => onToggle(asset.id)}
          />
          <Check size={13} strokeWidth={3} />
        </label>

        {asset.visibility !== 'personal' && view !== 'trash' && (
          <span className="absolute right-1.5 top-1.5 drop-shadow-sm">
            <Chip tone={asset.visibility === 'team' ? 'primary' : 'ok'}>
              {asset.visibility === 'team' ? '团队' : '小组'}
            </Chip>
          </span>
        )}
      </div>

      <div className="flex min-h-0 flex-1 flex-col gap-0.5 p-2.5">
        <button
          type="button"
          onClick={() => onAction('open', asset)}
          className="truncate text-left text-[14.5px] font-medium leading-5 text-ink hover:text-primary-deep"
          title={asset.name}
        >
          {title}
        </button>
        <p className="truncate text-[13px] leading-4 text-ink-mute">{context.join(' · ')}</p>
        <p className="min-h-4 truncate text-[12.5px] leading-4 text-ink-mute/85">
          {facts.join(' · ')}
        </p>

        {/* 标签排在体积下方：先看「是什么、多大」，再看「归在哪一档」。
            全部摊开、允许换行，不再用「两枚 +N」把后面的标签藏起来；卡片会
            因为标签多而变高，但网格里每张都以 `mt-auto` 把操作行压在底部，
            不会出现按钮参差。× 就地去掉、+ 就地添加，两者共用 PATCH tagIds。
            一个标签都没有时也要留着 +，否则「给第一条素材打标」没有入口。 */}
        {(tags.length > 0 || canRemoveTags) && (
          <div className="flex flex-wrap items-center gap-1 pt-0.5">
            {tags.map((tag) => (
              <span
                key={tag.id}
                className="inline-flex max-w-full items-center gap-0.5 rounded-[5px] border border-line-soft bg-panel-soft px-1.5 py-px text-[12px] leading-[15px] text-ink-soft"
              >
                <span className="max-w-[110px] truncate" title={tagLabel(tag)}>
                  {tagLabel(tag)}
                </span>
                {canRemoveTags && (
                  <button
                    type="button"
                    disabled={tagBusy}
                    onClick={() => onRemoveTag(asset, tag.id)}
                    aria-label={`移除标签 ${tagLabel(tag)}`}
                    title={`移除标签 ${tagLabel(tag)}`}
                    className="grid size-[14px] shrink-0 place-items-center rounded-full text-ink-mute transition-colors hover:bg-danger-soft hover:text-danger-deep focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 disabled:opacity-40"
                  >
                    <X size={10} strokeWidth={2.5} />
                  </button>
                )}
              </span>
            ))}
            {canRemoveTags && (
              <button
                type="button"
                disabled={tagBusy}
                onClick={() => onAddTag(asset)}
                aria-label={`给 ${title} 添加标签`}
                title="添加标签"
                className="inline-flex size-[19px] shrink-0 items-center justify-center rounded-[5px] border border-dashed border-primary/50 bg-primary-soft/40 text-primary-deep transition-colors hover:border-primary hover:bg-primary-soft hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 disabled:opacity-40"
              >
                <Plus size={11} strokeWidth={2.5} />
              </button>
            )}
          </div>
        )}

        {/* 主操作（入库 / 驳回 / 恢复）保留文字：那是这个入口里唯一的动作，图标
            猜不出来。次要动作改成图标，四个文字按钮并排会把 196px 的卡片撑成两行
            换行，网格看上去参差不齐。 */}
        <div className="mt-auto flex items-center gap-1 pt-1.5">
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

          <div className="ml-auto flex items-center gap-0.5">
            {view !== 'drafts' && view !== 'trash' && offer === 'revoke' && (
              <IconButton
                icon={<Undo2 size={13} />}
                label="取消共享（需审批通过后才能删除）"
                onClick={() => onAction('revoke', asset)}
              />
            )}

            {view !== 'drafts' && view !== 'trash' && offer !== 'revoke' && (
              <IconButton
                icon={<Share2 size={13} />}
                label={asset.visibility === 'personal' ? '共享到…' : '改共享'}
                onClick={() => onAction('share', asset)}
              />
            )}

            {view !== 'drafts' && view !== 'trash' && offer === 'trash' && (
              <IconButton
                icon={<Trash2 size={13} />}
                label="移入回收站"
                className="text-danger/80 hover:bg-danger-soft hover:text-danger-deep"
                onClick={() => onAction('delete', asset)}
              />
            )}

            {view !== 'drafts' && view !== 'trash' && offer === 'force' && (
              <IconButton
                icon={<Trash2 size={13} />}
                label="强制移入回收站（需填理由）"
                className="text-danger/80 hover:bg-danger-soft hover:text-danger-deep"
                onClick={() => onAction('delete', asset)}
              />
            )}

            {view !== 'drafts' && view !== 'trash' && asset.state === 'active' && (
              <IconButton
                icon={<Download size={13} />}
                /* 封装没有原文件：点它下到的是成员 zip，文案要说清，
                   否则用户以为「下载这个封装」和「下载原件」是同一件事 */
                label={asset.kind === 'pack' ? '打包下载其中素材' : '下载原文件'}
                onClick={() => onAction('download', asset)}
              />
            )}
          </div>
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
