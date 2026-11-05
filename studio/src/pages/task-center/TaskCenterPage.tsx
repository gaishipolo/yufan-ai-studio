import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { FolderOpen } from 'lucide-react';
import { PageHeader, CardSkeletonGrid, EmptyState, ErrorState, Pager, Tabs, type TabItem } from '@/shared/ui';
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
  /* 页码也放 URL：刷新/回退/分享要停在同一条记录上，和筛选条件同理 */
  const page = Math.max(1, Number(searchParams.get('page') ?? '1') || 1);
  const pageSize = 48;

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

  /** 改筛选条件就回到第 1 页：否则会出现「第 3 页但没有结果」 */
  const patchFilter = useCallback(
    (patch: Record<string, string | undefined>) => patchParams({ ...patch, page: undefined }),
    [patchParams],
  );

  useEffect(() => {
    if (debouncedKeyword !== keyword) patchFilter({ keyword: debouncedKeyword });
  }, [debouncedKeyword, keyword, patchFilter]);

  const params = useMemo<TaskListParams>(
    () => ({ status, keyword, sort, scope, onlyAuthorized, page, pageSize }),
    [status, keyword, sort, scope, onlyAuthorized, page],
  );

  const { data, isLoading, isFetching, isError, error, refetch } = useTaskList(params);
  const items = data?.items ?? [];
  const stats = data?.stats;
  const canReadAll = data?.canReadAll ?? false;
  // 0 = 还不知道总数（加载中 / 失败）。越界回收统一由 Pager 做，这里只负责把"不知道"如实传下去
  const totalPages = data ? Math.max(1, Math.ceil(data.total / (data.pageSize ?? pageSize))) : 0;

  /* ---------- 无跨用户权限时，URL 里残留的 scope=all/team 要回落到 mine，
     否则后端静默降级、下拉里又没有对应选项，筛选状态会悬空 ---------- */
  useEffect(() => {
    if (data && !canReadAll && scope !== 'mine') {
      patchFilter({ scope: 'mine' });
    }
  }, [data, canReadAll, scope, patchFilter]);

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

  /* 统计未知时不显示 0：请求失败时「全部 0」会让人以为任务被清空了 */
  const statusTabs: TabItem<StatusFilter>[] = [
    { value: 'all', label: '全部', count: stats?.all },
    { value: 'running', label: '进行中', count: stats?.running },
    { value: 'completed', label: '已完成', count: stats?.completed },
    { value: 'failed', label: '失败', count: stats?.failed },
  ];

  return (
    <>
      <PageHeader
        title="任务中心"
        description="生成任务异步执行，自动轮询结果，完成后视频可直接收藏、下载或二次剪辑。"
        actions={
          <TaskToolbar
            scope={scope}
            onScopeChange={(v) => patchFilter({ scope: v })}
            canReadAll={canReadAll}
            onlyAuthorized={onlyAuthorized}
            onAuthorizedChange={(v) => patchFilter({ auth: v ? '1' : undefined })}
            sort={sort}
            onSortChange={(v) => patchFilter({ sort: v })}
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
        total={isError ? undefined : data?.total}
      />

      <div className="mt-4 border-b border-line-soft pb-3">
        <Tabs items={statusTabs} value={status} onChange={(v) => patchFilter({ status: v })} />
      </div>

      <div className="mt-3.5">
        {isLoading ? (
          <CardSkeletonGrid count={8} />
        ) : isError ? (
          <ErrorState error={error} title="任务加载失败" onRetry={() => void refetch()} />
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

      {/* 一页 48 条、标题显示 total（全量）：没有翻页入口时第 49 条起在界面上不存在 */}
      <Pager
        page={page}
        totalPages={totalPages}
        busy={isFetching}
        onChange={(next) => patchParams({ page: next === 1 ? undefined : String(next) })}
        className="mt-3 rounded-card border border-line-soft bg-panel"
      />

      <TaskPreviewModal taskId={previewId} onClose={() => setPreviewId(null)} />
      <CreateTaskModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={() => {
          patchFilter({ status: 'all' });
          setSelected(new Set());
        }}
      />
    </>
  );
}
