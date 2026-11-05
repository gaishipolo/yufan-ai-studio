import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { FolderOpen } from 'lucide-react';
import { PageHeader, CardSkeletonGrid, EmptyState, Tabs, type TabItem } from '@/shared/ui';
import { toast } from '@/shared/lib/toast';
import { useDebouncedValue } from '@/shared/hooks/useDebouncedValue';
import { TaskCard } from '@/entities/task/ui/TaskCard';
import { useTaskList } from '@/entities/task/api/task.queries';
import type { TaskListParams, TaskScope, TaskSort, TaskStatus } from '@/entities/task/model/types';
import { TaskToolbar } from '@/features/task-filter/ui/TaskToolbar';
import { TaskSearchRow } from '@/features/task-filter/ui/TaskSearchRow';
import { TaskPreviewModal } from '@/features/task-preview/TaskPreviewModal';
import { CreateTaskModal } from '@/features/create-task/CreateTaskModal';

type StatusFilter = TaskStatus | 'all';

export default function TaskCenterPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);

  /* ---------- 筛选条件全部由 URL 承载：可分享 / 可回退 / 刷新不丢 ---------- */
  const status = (searchParams.get('status') ?? 'all') as StatusFilter;
  const keyword = searchParams.get('keyword') ?? '';
  const sort = (searchParams.get('sort') ?? 'createdAt_desc') as TaskSort;
  const scope = (searchParams.get('scope') ?? 'mine') as TaskScope;
  const onlyAuthorized = searchParams.get('auth') === '1';

  const patchParams = useCallback(
    (patch: Record<string, string | undefined>) => {
      const next = new URLSearchParams(searchParams);
      Object.entries(patch).forEach(([key, value]) => {
        if (value === undefined || value === '') next.delete(key);
        else next.set(key, value);
      });
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  /* ---------- 搜索防抖：输入 → 300ms 后写回 URL ---------- */
  const [keywordInput, setKeywordInput] = useState(keyword);
  const debouncedKeyword = useDebouncedValue(keywordInput, 300);

  useEffect(() => {
    if (debouncedKeyword !== keyword) patchParams({ keyword: debouncedKeyword });
  }, [debouncedKeyword, keyword, patchParams]);

  const params = useMemo<TaskListParams>(
    () => ({ status, keyword, sort, scope, onlyAuthorized, page: 1, pageSize: 48 }),
    [status, keyword, sort, scope, onlyAuthorized],
  );

  const { data, isLoading, isFetching, refetch } = useTaskList(params);
  const items = data?.items ?? [];
  const stats = data?.stats;
  const canReadAll = data?.canReadAll ?? false;

  /* ---------- 无跨用户权限时，URL 里残留的 scope=all/team 要回落到 mine，
     否则后端静默降级、下拉里又没有对应选项，筛选状态会悬空 ---------- */
  useEffect(() => {
    if (data && !canReadAll && scope !== 'mine') {
      patchParams({ scope: 'mine' });
    }
  }, [data, canReadAll, scope, patchParams]);

  /* ---------- 选择 ---------- */
  const toggleSelect = useCallback((id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const toggleSelectAll = () => {
    setSelected((prev) => {
      const allSelected = items.length > 0 && items.every((t) => prev.has(t.id));
      if (allSelected) return new Set();
      return new Set(items.map((t) => t.id));
    });
  };

  const statusTabs: TabItem<StatusFilter>[] = [
    { value: 'all', label: '全部', count: stats?.all ?? 0 },
    { value: 'running', label: '进行中', count: stats?.running ?? 0 },
    { value: 'completed', label: '已完成', count: stats?.completed ?? 0 },
    { value: 'failed', label: '失败', count: stats?.failed ?? 0 },
  ];

  return (
    <>
      <PageHeader
        title="任务中心"
        description="生成任务异步执行，自动轮询结果，完成后视频可直接收藏、下载或二次剪辑。"
        actions={
          <TaskToolbar
            scope={scope}
            onScopeChange={(v) => patchParams({ scope: v })}
            canReadAll={canReadAll}
            onlyAuthorized={onlyAuthorized}
            onAuthorizedChange={(v) => patchParams({ auth: v ? '1' : undefined })}
            sort={sort}
            onSortChange={(v) => patchParams({ sort: v })}
            selectedCount={selected.size}
            onSelectAll={toggleSelectAll}
            onDownload={() =>
              selected.size
                ? toast(`准备下载 ${selected.size} 个视频（待接入下载接口）`)
                : toast('请先勾选需要下载的任务', 'error')
            }
            onCollect={() => toast(selected.size ? '已加入收藏' : '请先选择任务', selected.size ? 'success' : 'error')}
            onRecycle={() => toast('回收站为空')}
            onCreate={() => setCreateOpen(true)}
          />
        }
      />

      <TaskSearchRow
        keyword={keywordInput}
        onKeywordChange={setKeywordInput}
        onRefresh={() => {
          void refetch();
          toast('已刷新任务列表');
        }}
        isFetching={isFetching}
        total={data?.total ?? 0}
      />

      <div className="mt-4 border-b border-line-soft pb-3">
        <Tabs items={statusTabs} value={status} onChange={(v) => patchParams({ status: v })} />
      </div>

      <div className="mt-3.5">
        {isLoading ? (
          <CardSkeletonGrid count={8} />
        ) : items.length === 0 ? (
          <EmptyState
            icon={<FolderOpen size={40} strokeWidth={1.4} />}
            title="没有匹配的任务"
            description="换个筛选条件或清空搜索关键字再试试"
          />
        ) : (
          <div className="grid grid-cols-[repeat(auto-fill,minmax(148px,1fr))] gap-2.5 md:grid-cols-[repeat(auto-fill,minmax(196px,1fr))] md:gap-3.5">
            {items.map((task) => (
              <TaskCard
                key={task.id}
                task={task}
                selected={selected.has(task.id)}
                onToggleSelect={toggleSelect}
                onOpen={setPreviewId}
              />
            ))}
          </div>
        )}
      </div>

      <TaskPreviewModal taskId={previewId} onClose={() => setPreviewId(null)} />
      <CreateTaskModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={() => {
          patchParams({ status: 'all' });
          setSelected(new Set());
        }}
      />
    </>
  );
}
