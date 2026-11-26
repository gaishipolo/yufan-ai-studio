import { Copy, Eye, Pencil, Power, PowerOff, RotateCcw, Trash2 } from 'lucide-react';
import type { TemplateCaseOut } from '@/shared/api/schema';
import type { CaseScope } from '@/entities/template-case/api/case.api';
import { CASE_STATUS_LABELS, formatTime } from '@/entities/template-case/model/caseMeta';
import { Button, Chip } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';

export interface CaseCardProps {
  item: TemplateCaseOut;
  /** 当前口径：公共库 / 我发布的 / 回收站 —— 三个口径能做的动作不一样。 */
  scope: CaseScope;
  busy: boolean;
  /** 有没有管理权（`template_pack:case:write`）。没有就只留查看与复用。 */
  canManage: boolean;
  onDetail: () => void;
  onReuse: () => void;
  onEdit: () => void;
  onOffline: () => void;
  onOnline: () => void;
  onRemove: () => void;
  onRestore: () => void;
}

/**
 * 案例卡片。
 *
 * 案例没有封面（它是一份**配置**，不是一条素材），所以卡片是纯文字的：名称 +
 * 描述 + 关联模板 + 分类 + 谁发布的。
 *
 * 「关联模板」这一行是这张卡的重点 —— 案例与模板的关系（案例 = 模板草稿的一份
 * 快照）必须一眼看出来，否则用户会以为案例是独立的东西。
 */
export function CaseCard({
  item,
  scope,
  busy,
  canManage,
  onDetail,
  onReuse,
  onEdit,
  onOffline,
  onOnline,
  onRemove,
  onRestore,
}: CaseCardProps) {
  const trashed = scope === 'trash';
  const offline = item.status === 'offline';
  /** 只有已发布的案例能复用。 */
  const reusable = item.status === 'published';

  return (
    <div
      className={cn(
        'flex flex-col gap-2 rounded-2xl border border-line-soft bg-panel p-3',
        'transition-all duration-200',
        'hover:border-line-strong',
      )}
    >
      <div className="flex items-start gap-2">
        <button
          type="button"
          onClick={onDetail}
          className="min-w-0 flex-1 truncate text-left text-body font-medium text-ink transition-colors hover:text-primary"
        >
          {item.name}
        </button>
        {trashed ? (
          <Chip tone="danger">回收站</Chip>
        ) : (
          <Chip tone={offline ? 'warn' : 'ok'}>{CASE_STATUS_LABELS[item.status] ?? item.status}</Chip>
        )}
      </div>

      {item.description && (
        <p className="line-clamp-2 text-meta leading-[1.5] text-ink-soft">{item.description}</p>
      )}

      {/* 关联模板：案例是这份模板的快照。源模板被删了也不影响复用（案例自带
          快照），但要说明白 —— 否则用户以为这份案例坏了。 */}
      <div className="flex flex-wrap items-center gap-1.5 text-micro text-ink-mute">
        <span className="shrink-0">关联模板</span>
        <span className="truncate font-medium text-ink-soft" title={item.specKey}>
          {item.specName || item.specKey}
        </span>
        <span className="shrink-0 text-line-strong">·</span>
        <span className="shrink-0 tabular-nums">v{item.specVersion}</span>
        {item.sourceMissing && <Chip tone="warn">源模板已删除</Chip>}
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        {item.category && <Chip>{item.category}</Chip>}
        <span className="truncate text-micro text-ink-mute">
          {trashed
            ? `删除于 ${formatTime(item.updatedAt)}`
            : `${item.ownerName || '未知'} · ${formatTime(item.publishedAt ?? item.createdAt)}`}
        </span>
      </div>

      <div className="mt-1 flex items-center gap-1.5 border-t border-line-soft pt-2">
        {trashed ? (
          <Button size="sm" icon={<RotateCcw size={11} />} disabled={busy} onClick={onRestore}>
            恢复
          </Button>
        ) : (
          <>
            <Button size="sm" icon={<Eye size={11} />} onClick={onDetail}>
              查看
            </Button>
            <Button
              size="sm"
              icon={<Copy size={11} />}
              className="text-primary hover:border-primary-line hover:bg-primary-soft hover:text-primary-deep"
              /* 下架的案例不能复用（后端只让复用已发布的），
                 摆一个点了就报错的按钮没有意义。 */
              disabled={!reusable}
              onClick={onReuse}
            >
              复用
            </Button>
            {/* 管理动作只在「我发布的」里出现：公共案例库里全是别人的案例，
                编辑/下架/删除一定 403（后端只允许作者操作自己的案例）。 */}
            {canManage && scope === 'mine' && (
              <>
                <Button size="sm" icon={<Pencil size={11} />} disabled={busy} onClick={onEdit}>
                  编辑
                </Button>
                {offline ? (
                  <Button size="sm" icon={<Power size={11} />} disabled={busy} onClick={onOnline}>
                    上架
                  </Button>
                ) : (
                  <Button
                    size="sm"
                    icon={<PowerOff size={11} />}
                    className="hover:border-warn-line hover:bg-warn-soft hover:text-warn-deep"
                    disabled={busy}
                    onClick={onOffline}
                  >
                    下架
                  </Button>
                )}
                <Button
                  size="sm"
                  icon={<Trash2 size={11} />}
                  className="ml-auto hover:border-danger-line hover:bg-danger-soft hover:text-danger"
                  disabled={busy}
                  onClick={onRemove}
                >
                  删除
                </Button>
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}
