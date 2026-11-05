import { Bookmark, Download, Plus, Trash2 } from 'lucide-react';
import { Button, Select, Switch } from '@/shared/ui';
import type { TaskScope, TaskSort } from '@/entities/task/model/types';

const SCOPE_OPTIONS = [
  { value: 'mine', label: '我的任务' },
  { value: 'all', label: '全部任务' },
  // 'team'（团队任务）：后端团队数据范围尚未实现（当前与 all 同义），实现后再放出
];

const MINE_ONLY = [SCOPE_OPTIONS[0]];

const SORT_OPTIONS = [
  { value: 'createdAt_desc', label: '按创建时间' },
  { value: 'createdAt_asc', label: '按创建时间（旧）' },
  { value: 'title_asc', label: '按名称' },
];

export interface TaskToolbarProps {
  scope: TaskScope;
  onScopeChange: (value: TaskScope) => void;
  /** 无 `task:read:all` 时后端会静默降级为「只看自己」，
   *  不收起范围选项的话，用户会以为「全部任务」里就是全站数据 */
  canReadAll: boolean;
  onlyAuthorized: boolean;
  onAuthorizedChange: (value: boolean) => void;
  sort: TaskSort;
  onSortChange: (value: TaskSort) => void;
  selectedCount: number;
  onSelectAll: () => void;
  onDownload: () => void;
  onCollect: () => void;
  onRecycle: () => void;
  onCreate: () => void;
}

export function TaskToolbar({
  scope,
  onScopeChange,
  canReadAll,
  onlyAuthorized,
  onAuthorizedChange,
  sort,
  onSortChange,
  selectedCount,
  onSelectAll,
  onDownload,
  onCollect,
  onRecycle,
  onCreate,
}: TaskToolbarProps) {
  return (
    <>
      <Select
        options={canReadAll ? SCOPE_OPTIONS : MINE_ONLY}
        value={scope}
        onChange={(e) => onScopeChange(e.target.value as TaskScope)}
      />
      <Switch checked={onlyAuthorized} onChange={onAuthorizedChange}>
        仅授权
      </Switch>

      <Button size="sm" icon={<Download size={13} />} onClick={onDownload}>
        下载<span className="text-ink-mute">({selectedCount})</span>
      </Button>
      <Button size="sm" icon={<Bookmark size={13} />} onClick={onCollect}>
        收藏
      </Button>
      <Button size="sm" icon={<Trash2 size={13} />} onClick={onRecycle}>
        回收站
      </Button>
      <Button size="sm" variant="ghost" onClick={onSelectAll}>
        全选
      </Button>
      <Select
        options={SORT_OPTIONS}
        value={sort}
        onChange={(e) => onSortChange(e.target.value as TaskSort)}
      />
      <Button size="sm" variant="primary" icon={<Plus size={13} />} onClick={onCreate}>
        创建任务
      </Button>
    </>
  );
}
