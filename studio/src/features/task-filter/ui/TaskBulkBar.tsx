import { Bookmark, Download, Trash2, X } from 'lucide-react';
import { Button } from '@/shared/ui';

/**
 * 选中后才出现的批量操作条。
 *
 * 以前「下载(0) / 收藏 / 删除 / 全选」常驻在页头：没选任何东西时它们是四个不可用的
 * 按钮，占着最显眼的位置。现在没选中就只有一个「从模板生成」的入口，选中后这一条
 * 才出现，并明确写着选了几项。
 *
 * 只作用于通用视频任务：生图与模板替换没有批量删除接口（见页面注释）。
 */
export function TaskBulkBar({
  count,
  busy,
  onSelectAll,
  onClear,
  onDownload,
  onCollect,
  onDelete,
}: {
  count: number;
  busy?: boolean;
  onSelectAll: () => void;
  onClear: () => void;
  onDownload: () => void;
  onCollect: () => void;
  onDelete: () => void;
}) {
  if (count <= 0) return null;
  return (
    <div className="flex items-center gap-1.5 rounded-lg border border-primary-line bg-primary-soft/60 py-1 pl-2.5 pr-1">
      <span className="text-[11px] font-medium tabular-nums text-primary-deep">已选 {count} 项</span>
      <span className="mx-0.5 h-3.5 w-px bg-primary-line" aria-hidden />
      <Button size="sm" variant="ghost" icon={<Download size={12} />} onClick={onDownload}>
        下载
      </Button>
      <Button
        size="sm"
        variant="ghost"
        icon={<Bookmark size={12} />}
        disabled={busy}
        onClick={onCollect}
      >
        收藏
      </Button>
      <Button
        size="sm"
        variant="ghost"
        icon={<Trash2 size={12} />}
        disabled={busy}
        onClick={onDelete}
        className="text-ink-soft hover:text-danger"
      >
        删除
      </Button>
      <span className="mx-0.5 h-3.5 w-px bg-primary-line" aria-hidden />
      <Button size="sm" variant="ghost" onClick={onSelectAll}>
        全选
      </Button>
      <button
        type="button"
        onClick={onClear}
        aria-label="取消选择"
        className="grid h-6 w-6 place-items-center rounded-md text-ink-mute transition-colors hover:bg-panel hover:text-ink"
      >
        <X size={12} />
      </button>
    </div>
  );
}
