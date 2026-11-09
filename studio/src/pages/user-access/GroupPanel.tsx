import { useState } from 'react';
import { AlertCircle, Pencil, Plus, Trash2 } from 'lucide-react';
import { Button, Skeleton } from '@/shared/ui';
import { Can } from '@/shared/access/Can';
import { cn } from '@/shared/lib/cn';
import { toast } from '@/shared/lib/toast';
import { useDeleteGroup, useGroupList } from '@/entities/group/api/group.queries';
import type { Group } from '@/entities/group/model/types';

/**
 * 左侧「分组」栏（截图布局）。
 *
 * 选中的是 `groupId` 而不是小组对象：右侧用户列表的筛选只需要一个 id，
 * 传整个对象会让「改了组名」也触发列表重取，还会把筛选条件和展示数据绑在一起。
 *
 * 「全部分组」是「不过滤」（`null`），不是 id 为 `__all__` 的实体 —— 后端
 * 团队层用的 `__all__` 是权限授予的固定 scope_id，两者不是一回事。
 */
export function GroupPanel({
  selectedId,
  onSelect,
  onCreate,
  onEdit,
}: {
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onCreate: () => void;
  onEdit: (group: Group) => void;
}) {
  const groupsQuery = useGroupList();
  const deleteGroup = useDeleteGroup();
  const [confirmId, setConfirmId] = useState<string | null>(null);

  const groups = groupsQuery.data ?? [];

  const handleDelete = (group: Group) => {
    deleteGroup.mutate(group.id, {
      onSuccess: () => {
        toast('小组已删除', 'success');
        setConfirmId(null);
        if (selectedId === group.id) onSelect(null);
      },
      onError: (error) => {
        toast(error.message, 'error');
        setConfirmId(null);
      },
    });
  };

  return (
    <aside className="w-full shrink-0 rounded-card border border-line-soft bg-panel md:w-[212px]">
      <header className="flex items-center gap-1.5 border-b border-line-soft px-3 py-2.5">
        <h3 className="text-[12.5px] font-semibold text-ink">分组</h3>
        <Can permission="identity:group:create">
          <button
            type="button"
            className="ml-auto inline-flex items-center gap-0.5 text-[11.5px] text-primary hover:underline"
            onClick={onCreate}
          >
            <Plus size={12} /> 新增分组
          </button>
        </Can>
      </header>

      {groupsQuery.isLoading ? (
        <div className="space-y-2 p-3">
          {Array.from({ length: 3 }).map((_, index) => (
            <Skeleton key={index} className="h-6 w-full" />
          ))}
        </div>
      ) : groupsQuery.isError ? (
        <div className="flex flex-col items-center gap-2 px-3 py-8 text-center">
          <AlertCircle size={18} className="text-danger" />
          <p className="text-[11px] text-ink-soft">分组加载失败：{groupsQuery.error.message}</p>
          <Button size="sm" onClick={() => void groupsQuery.refetch()}>
            重试
          </Button>
        </div>
      ) : (
        <div className="max-h-[520px] overflow-auto p-1.5">
          <button
            type="button"
            className={cn(
              'flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-xs transition-colors',
              selectedId === null ? 'bg-primary-soft font-medium text-primary' : 'text-ink hover:bg-panel-soft',
            )}
            onClick={() => onSelect(null)}
          >
            <span>全部分组</span>
            <span className="ml-auto text-[11px] tabular-nums text-ink-mute" title="小组数量">
              ({groups.length})
            </span>
          </button>

          {groups.length === 0 ? (
            <p className="px-2.5 py-4 text-[11px] leading-[1.6] text-ink-mute">
              还没有小组。新建后可以给组分配成员与整体权限。
            </p>
          ) : (
            <ul className="mt-0.5 space-y-0.5">
              {groups.map((group) => (
                <li
                  key={group.id}
                  className={cn(
                    'group flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-xs transition-colors',
                    selectedId === group.id
                      ? 'bg-primary-soft font-medium text-primary'
                      : 'text-ink hover:bg-panel-soft',
                  )}
                >
                  <button
                    type="button"
                    className="min-w-0 flex-1 truncate text-left"
                    title={group.description || group.name}
                    onClick={() => onSelect(group.id)}
                  >
                    <span className="truncate">{group.name}</span>
                    <span className="ml-1.5 text-[10.5px] tabular-nums text-ink-mute">
                      {group.memberCount ?? group.memberIds?.length ?? 0}
                    </span>
                  </button>

                  {confirmId === group.id ? (
                    <span className="flex shrink-0 items-center gap-1 text-[10.5px]">
                      <button
                        type="button"
                        className="text-danger hover:underline"
                        disabled={deleteGroup.isPending}
                        onClick={() => handleDelete(group)}
                      >
                        确认
                      </button>
                      <button type="button" className="text-ink-mute hover:underline" onClick={() => setConfirmId(null)}>
                        取消
                      </button>
                    </span>
                  ) : (
                    <span className="flex shrink-0 items-center gap-0.5">
                      <Can permission="identity:group:update">
                        <button
                          type="button"
                          aria-label={`编辑小组 ${group.name}`}
                          title="编辑小组"
                          className="grid h-5 w-5 place-items-center rounded text-ink-mute hover:bg-white hover:text-primary"
                          onClick={() => onEdit(group)}
                        >
                          <Pencil size={11} />
                        </button>
                      </Can>
                      <Can permission="identity:group:delete">
                        <button
                          type="button"
                          aria-label={`删除小组 ${group.name}`}
                          title="删除小组（同时清成员与授权）"
                          className="grid h-5 w-5 place-items-center rounded text-ink-mute hover:bg-white hover:text-danger"
                          onClick={() => setConfirmId(group.id)}
                        >
                          <Trash2 size={11} />
                        </button>
                      </Can>
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </aside>
  );
}
