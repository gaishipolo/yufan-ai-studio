import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { FolderOpen, Info } from 'lucide-react';
import { PageHeader, CardSkeletonGrid, EmptyState, ErrorState, Pager, Select, Tabs, type TabItem } from '@/shared/ui';
import { toast } from '@/shared/lib/toast';
import { downloadUrl } from '@/shared/lib/download';
import { useCan } from '@/shared/access/use-access';
import { useDebouncedValue } from '@/shared/hooks/useDebouncedValue';
import { TaskCard } from '@/entities/task/ui/TaskCard';
import { ImageTaskCard } from '@/entities/task/ui/ImageTaskCard';
import { useDeleteTasks, useTaskFeed, useUpdateTask } from '@/entities/task/api/task.queries';
import type { TaskScope, TaskSort } from '@/entities/task/model/types';
import type { TaskFeedItem, TaskFeedParams, TaskFeedSource, TaskFeedStatus } from '@/entities/task/model/feed.types';
import { REPLACEMENT_STATUS } from '@/entities/template/model/replacement.labels';
import { TaskToolbar } from '@/features/task-filter/ui/TaskToolbar';
import { TaskSearchRow } from '@/features/task-filter/ui/TaskSearchRow';
import { TaskPreviewModal } from '@/features/task-preview/TaskPreviewModal';
import { ImagePreviewModal } from '@/features/task-preview/ImagePreviewModal';
import { CreateTaskModal } from '@/features/create-task/CreateTaskModal';

type StatusFilter = TaskFeedStatus;

/** 成片文件名：视频/替换档案固定 mp4，生图按真实地址后缀，别让用户下载到无后缀文件 */
function artifactName(item: TaskFeedItem): string {
  const url = item.outputUrl ?? '';
  const suffix = item.source === 'image'
    ? (/\.(png|jpe?g|webp|gif)/i.exec(url.split('?')[0] ?? '')?.[0] ?? '.png')
    : '.mp4';
  return `${item.title.replace(/[\\/:*?"<>|]/g, '_')}${suffix}`;
}

export default function TaskCenterPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [preview, setPreview] = useState<TaskFeedItem | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const canReadTemplates = useCan('template:read');
  const canReadImages = useCan('image:read');

  /* ---------- 筛选条件全部由 URL 承载：可分享 / 可回退 / 刷新不丢 ---------- */
  const statusParam = searchParams.get('status') ?? 'all';
  const status = (['all', 'queued', 'running', 'completed', 'failed', 'attention'].includes(statusParam) ? statusParam : 'all') as StatusFilter;
  const sourceParam = searchParams.get('source') ?? 'all';
  const source = (['all', 'video', 'image', 'replacement'].includes(sourceParam) ? sourceParam : 'all') as TaskFeedSource;
  const keyword = searchParams.get('keyword') ?? '';
  const sortParam = searchParams.get('sort') ?? 'createdAt_desc';
  const sort = (['createdAt_desc', 'createdAt_asc', 'title_asc'].includes(sortParam) ? sortParam : 'createdAt_desc') as TaskSort;
  const scope = (searchParams.get('scope') === 'all' ? 'all' : 'mine') as TaskScope;
  const onlyAuthorized = searchParams.get('auth') === '1';
  /* 页码也放 URL：刷新/回退/分享要停在同一条记录上，和筛选条件同理 */
  const page = Math.max(1, Math.floor(Number(searchParams.get('page') ?? '1') || 1));
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
    (patch: Record<string, string | undefined>) => {
      setSelected(new Set());
      patchParams({ ...patch, page: undefined });
    },
    [patchParams],
  );

  useEffect(() => {
    if (debouncedKeyword !== keyword) patchFilter({ keyword: debouncedKeyword });
  }, [debouncedKeyword, keyword, patchFilter]);

  const params = useMemo<TaskFeedParams>(
    () => ({ source, status, keyword, sort, scope, onlyAuthorized, page, pageSize }),
    [source, status, keyword, sort, scope, onlyAuthorized, page],
  );

  const { data, isLoading, isFetching, isError, error, refetch } = useTaskFeed(params);
  const deleteTasks = useDeleteTasks();
  const updateTask = useUpdateTask();
  // 用 useMemo 稳住引用：`openPreview` 依赖 items，裸的 `?? []` 每次渲染都会换数组
  const items = useMemo(() => data?.items ?? [], [data?.items]);
  const videoItems = items.flatMap(item => item.video ? [item.video] : []);
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

  /* ---------- 选择：只有通用视频任务支持批量删除/收藏 ---------- */
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
      const allSelected = videoItems.length > 0 && videoItems.every((t) => prev.has(t.id));
      if (allSelected) return new Set();
      return new Set(videoItems.map((t) => t.id));
    });
  };

  const openPreview = useCallback(
    (id: string) => setPreview(items.find(item => item.id === id) ?? null),
    [items],
  );

  const handleDownload = () => {
    if (!selected.size) return toast('请先勾选需要下载的任务', 'error');
    const ready = items.filter(item => selected.has(item.id) && item.outputUrl);
    if (!ready.length) return toast('勾选的任务还没有成片', 'error');
    ready.forEach(item => item.outputUrl && downloadUrl(item.outputUrl, artifactName(item)));
    toast(
      `已打开 ${ready.length} 个成片${ready.length < selected.size ? `，${selected.size - ready.length} 个暂无成片` : ''}`,
      'success',
    );
  };

  const handleCollect = async () => {
    if (!selected.size) return toast('请先勾选需要收藏的任务', 'error');
    try {
      await Promise.all([...selected].map(id => updateTask.mutateAsync({ id, favorited: true })));
      toast(`已收藏 ${selected.size} 个任务`, 'success');
      setSelected(new Set());
    } catch (e) {
      toast(e instanceof Error ? e.message : '收藏失败', 'error');
    }
  };

  const handleDelete = async () => {
    if (!selected.size) return toast('请先勾选要删除的任务', 'error');
    try {
      const result = await deleteTasks.mutateAsync([...selected]);
      toast(
        `已删除 ${result.deleted} 个任务${result.skipped.length ? `，${result.skipped.length} 个未删除` : ''}`,
        result.skipped.length ? 'error' : 'success',
      );
      setSelected(new Set());
    } catch (e) {
      toast(e instanceof Error ? e.message : '删除失败', 'error');
    }
  };

  /* 统计未知时不显示 0：请求失败时「全部 0」会让人以为任务被清空了 */
  const statusTabs: TabItem<StatusFilter>[] = [
    { value: 'all', label: '全部', count: stats?.all },
    { value: 'running', label: '进行中', count: stats?.running },
    { value: 'completed', label: '已完成', count: stats?.completed },
    { value: 'failed', label: '失败', count: stats?.failed },
    { value: 'attention', label: '待处理', count: stats?.attention },
  ];

  return (
    <>
      <PageHeader
        title="任务中心"
        description="统一跟踪视频生成、生图与模板替换任务，自动同步进度、成片与待处理事项。"
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
            onDownload={handleDownload}
            onCollect={handleCollect}
            onDelete={handleDelete}
            onCreate={() => setCreateOpen(true)}
            busy={deleteTasks.isPending || updateTask.isPending}
          />
        }
      />

      <p className="mt-2.5 flex flex-wrap items-center gap-1 text-[11.5px] leading-relaxed text-ink-mute">
        <Info size={12} className="shrink-0" aria-hidden />
        三类任务统一展示；生图与替换档案按各自权限只显示本人，完成后可查看成片与下载。批量操作仅适用于通用视频任务。
        {canReadImages && <Link to="/image" className="text-primary hover:underline">生图工作台 →</Link>}
        {canReadTemplates && <Link to="/templates" className="text-primary hover:underline">创建替换任务 →</Link>}
      </p>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Select aria-label="任务来源" value={source} onChange={event => patchFilter({source: event.target.value})}
          options={[{value: 'all', label: '全部来源'}, {value: 'video', label: '通用视频生成'},
            ...(canReadImages ? [{value: 'image', label: '生图'}] : []),
            ...(canReadTemplates ? [{value: 'replacement', label: '模板替换'}] : [])]} />
        {source === 'replacement' && !canReadTemplates && <p className="text-xs text-ink-mute">当前账号没有替换任务读取权限。</p>}
        {source === 'image' && !canReadImages && <p className="text-xs text-ink-mute">当前账号没有生图读取权限。</p>}
      </div>
      {data?.warnings.map(warning => <p key={warning} role="alert" className="mt-3 text-sm text-danger">{warning}</p>)}

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
            {items.map((item) => item.video ? (
              <TaskCard
                key={item.key}
                task={item.video}
                selected={selected.has(item.id)}
                onToggleSelect={toggleSelect}
                onOpen={openPreview}
              />
            ) : item.image ? (
              <ImageTaskCard key={item.key} item={item.image} onOpen={openPreview} />
            ) : (
              <article key={item.key} className="flex flex-col gap-3 rounded-card border border-line-soft bg-panel p-4">
                <span className="text-xs font-semibold text-primary">模板替换</span>
                <h2 className="break-words text-sm font-semibold text-ink">{item.title}</h2>
                <p className="text-xs text-ink-soft">{item.status === 'success' && !item.outputUrl ? '结果文件待恢复' : REPLACEMENT_STATUS[item.status] ?? item.status}</p>
                {item.group === 'running' && <p className="text-xs text-ink-mute">进度 {item.progress}%</p>}
                {item.errorMessage && <p className="break-words text-xs text-danger">{item.errorMessage}</p>}
                <p className="break-all text-[11px] text-ink-mute">任务编号：{item.id}</p>
                <time dateTime={item.createdAt} className="text-[11px] text-ink-mute">{new Date(item.createdAt).toLocaleString()}</time>
                <div className="mt-auto flex flex-wrap items-center gap-2">
                  {item.detailUrl && <Link to={item.detailUrl} className="text-xs text-primary hover:underline">{item.outputUrl ? '查看成片与档案 →' : '查看替换档案 →'}</Link>}
                  {item.outputUrl && (
                    <button
                      type="button"
                      onClick={() => downloadUrl(item.outputUrl as string, artifactName(item))}
                      className="text-xs text-primary hover:underline"
                    >
                      下载成片
                    </button>
                  )}
                </div>
              </article>
            ))}
          </div>
        )}
      </div>

      {/* 一页 48 条、标题显示 total（全量）：没有翻页入口时第 49 条起在界面上不存在 */}
      <Pager
        page={page}
        totalPages={totalPages}
        busy={isFetching}
        onChange={(next) => {setSelected(new Set()); patchParams({ page: next === 1 ? undefined : String(next) });}}
        className="mt-3 rounded-card border border-line-soft bg-panel"
      />

      <TaskPreviewModal
        taskId={preview?.source === 'video' ? preview.id : null}
        outputUrl={preview?.outputUrl}
        onClose={() => setPreview(null)}
      />
      <ImagePreviewModal
        item={preview?.source === 'image' ? preview : null}
        onClose={() => setPreview(null)}
      />
      <CreateTaskModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={() => {
          patchFilter({ source: 'all', status: 'all' });
          setSelected(new Set());
        }}
      />
    </>
  );
}
