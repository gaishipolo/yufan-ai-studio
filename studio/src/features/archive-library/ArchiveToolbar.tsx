import { UploadCloud } from 'lucide-react';
import { Button } from '@/shared/ui';
import { Can } from '@/shared/access/Can';
import { formatBytes } from '@/shared/lib/format';
import { ARCHIVE_CATEGORIES, ARCHIVE_LABELS, type ArchiveCategory } from '@/entities/asset/model/archive.types';

export interface ArchiveStats {
  total?: number;
  usedBytes?: number;
  /** 首次加载且没有可用数据：统计必须显示 —，不能显示 0。 */
  pending: boolean;
  /** 已有缓存、正在后台更新。 */
  refreshing: boolean;
}

interface ArchiveToolbarProps {
  category: ArchiveCategory;
  onCategoryChange: (value: ArchiveCategory) => void;
  stats: ArchiveStats;
  uploading: boolean;
  /** 批次在途：分类与上传入口一起锁住，避免同一批文件落到两个分类。 */
  locked?: boolean;
  onPick: () => void;
}

/** 分类、统计与上传入口。统计口径只描述当前分类的索引记录，不是磁盘占用。 */
export function ArchiveToolbar({category, onCategoryChange, stats, uploading, locked = false, onPick}: ArchiveToolbarProps) {
  const categories = ARCHIVE_CATEGORIES;
  const summary = stats.pending
    ? '当前分类 —'
    : `当前分类 ${stats.total ?? 0} 项 · 合计 ${formatBytes(stats.usedBytes ?? 0)}`;
  return <div className="flex flex-wrap items-center gap-2">
    {categories.map(key => <Button key={key} variant={category === key ? 'primary' : 'secondary'}
      aria-pressed={category === key} disabled={locked} aria-disabled={locked}
      onClick={() => onCategoryChange(key)}>{ARCHIVE_LABELS[key]}</Button>)}
    <span className="ml-auto text-xs text-ink-mute" aria-live="polite">
      {summary}{stats.refreshing ? ' · 更新中…' : ''}
    </span>
    <Can permission="asset:create">
      <Button icon={<UploadCloud size={14} />} loading={uploading} disabled={uploading || locked} onClick={onPick}>
        上传{ARCHIVE_LABELS[category]}
      </Button>
    </Can>
  </div>;
}
