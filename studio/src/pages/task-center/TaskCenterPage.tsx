import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { FolderOpen, Plus } from 'lucide-react';
import { Button, PageHeader, CardSkeletonGrid, EmptyState, ErrorState, Pager, Tabs, type TabItem } from '@/shared/ui';
import { toast } from '@/shared/lib/toast';
import { downloadUrl } from '@/shared/lib/download';
import { useCan } from '@/shared/access/use-access';
import { useDebouncedValue } from '@/shared/hooks/useDebouncedValue';
import { TaskCard } from '@/entities/task/ui/TaskCard';
import { ImageTaskCard } from '@/entities/task/ui/ImageTaskCard';
import { useDeleteImages, useDeleteTasks, useTaskFeed, useUpdateTask } from '@/entities/task/api/task.queries';
import type { TaskScope, TaskSort } from '@/entities/task/model/types';
import type { TaskFeedItem, TaskFeedParams, TaskFeedSource, TaskFeedStatus } from '@/entities/task/model/feed.types';
import { TaskFilterBar } from '@/features/task-filter/ui/TaskFilterBar';
import { TaskBulkBar } from '@/features/task-filter/ui/TaskBulkBar';
import { TaskPreviewModal } from '@/features/task-preview/TaskPreviewModal';
import { ImagePreviewModal } from '@/features/task-preview/ImagePreviewModal';
import { CreateTaskModal } from '@/features/create-task/CreateTaskModal';
import { generatedFileName } from '@/shared/lib/format';
import { useSessionStore } from '@/shared/access/session';

type StatusFilter = TaskFeedStatus;

/**
 * 成片文件名：`作者-年-月-日-时-分-秒` + 真实后缀（见 `generatedFileName`）。
 *
 * 后缀按来源真实产物给：视频固定 mp4，生图看地址本身——写死会让 png 存成 .mp4，
 * 下载下来双击打不开。作者取任务作者，生图记录没有作者字段时用当前账号。
 */
function artifactName(item: TaskFeedItem, me: string): string {
  const url = item.outputUrl ?? '';
  const suffix =
    item.source === 'image'
      ? (/\.(png|jpe?g|webp|gif)/i.exec(url.split('?')[0] ?? '')?.[0] ?? '.png')
      : '.mp4';
  const author = item.video?.authorName || me;
  const at = item.video?.createdAt ?? item.image?.createdAt ?? item.createdAt;
  return generatedFileName(author, at, suffix);
}

export default function TaskCenterPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [preview, setPreview] = useState<TaskFeedItem | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const canReadImages = useCan('image:read');
  const me = useSessionStore(state => state.subject.name);

  /* ---------- 筛选条件全部由 URL 承载：可分享 / 可回退 / 刷新不丢 ---------- */
  const statusParam = searchParams.get('status') ?? 'all';
  const status = (['all', 'queued', 'running', 'completed', 'failed', 'attention'].includes(statusParam) ? statusParam : 'all') as StatusFilter;
  const sourceParam = searchParams.get('source') ?? 'all';
  const source = (['all', 'video', 'image'].includes(sourceParam) ? sourceParam : 'all') as TaskFeedSource;
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

  /* 反向同步（URL → 输入框）：浏览器回退 / 点「清空筛选」时 URL 变了，
     输入框里的旧词不跟着清，用户会看到"框里有字但没有筛"的矛盾状态。 */
  useEffect(() => {
    setKeywordInput(current => (current === keyword ? current : keyword));
  }, [keyword]);

  const params = useMemo<TaskFeedParams>(
    () => ({ source, status, keyword, sort, scope, onlyAuthorized, page, pageSize }),
    [source, status, keyword, sort, scope, onlyAuthorized, page],
  );

  const { data, isLoading, isFetching, isError, error, refetch } = useTaskFeed(params);
  const deleteTasks = useDeleteTasks();
  const deleteImages = useDeleteImages();
  const updateTask = useUpdateTask();
  // 用 useMemo 稳住引用：`openPreview` 依赖 items，裸的 `?? []` 每次渲染都会换数组
  const items = useMemo(() => data?.items ?? [], [data?.items]);
  // 批量操作按来源分流：视频走 DELETE /tasks，生图走 DELETE /images。后端是两套表，
  // 一个端点认不了两种 id（生图 id 传进 /tasks 只会被 skipped）
  const sourceOf = useMemo(
    () => new Map(items.map(item => [item.id, item.source])),
    [items],
  );
  const deletableItems = useMemo(
    () => items.flatMap(item => (item.video || item.image ? [item] : [])),
    [items],
  );
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
      const allSelected =
        deletableItems.length > 0 && deletableItems.every((item) => prev.has(item.id));
      if (allSelected) return new Set();
      return new Set(deletableItems.map((item) => item.id));
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
    ready.forEach(item => item.outputUrl && downloadUrl(item.outputUrl, artifactName(item, me)));
    toast(
      `已打开 ${ready.length} 个产物${ready.length < selected.size ? `，${selected.size - ready.length} 个暂无产物` : ''}`,
      'success',
    );
  };

  const handleCollect = async () => {
    if (!selected.size) return toast('请先勾选需要收藏的任务', 'error');
    // 收藏只存在于视频任务（`PATCH /tasks/{id}`）；生图记录没有这个字段，
    // 混选时跳过它们并把话说清楚，而不是整批失败
    const videoIds = [...selected].filter(id => sourceOf.get(id) === 'video');
    if (!videoIds.length) return toast('生图记录暂不支持收藏', 'error');
    try {
      await Promise.all(videoIds.map(id => updateTask.mutateAsync({ id, favorited: true })));
      const skipped = selected.size - videoIds.length;
      toast(
        `已收藏 ${videoIds.length} 个任务${skipped > 0 ? `；${skipped} 条生图记录不支持收藏` : ''}`,
        'success',
      );
      setSelected(new Set());
    } catch (e) {
      toast(e instanceof Error ? e.message : '收藏失败', 'error');
    }
  };

  const handleDelete = async () => {
    if (!selected.size) return toast('请先勾选要删除的任务', 'error');
    const ids = [...selected];
    const videoIds = ids.filter(id => sourceOf.get(id) === 'video');
    const imageIds = ids.filter(id => sourceOf.get(id) === 'image');
    try {
      let deleted = 0;
      const skipped: string[] = [];
      if (videoIds.length) {
        const result = await deleteTasks.mutateAsync(videoIds);
        deleted += result.deleted;
        skipped.push(...result.skipped);
      }
      if (imageIds.length) {
        const result = await deleteImages.mutateAsync(imageIds);
        deleted += result.deleted;
        skipped.push(...result.skipped);
      }
      toast(
        `已删除 ${deleted} 个任务${skipped.length ? `，${skipped.length} 个未删除` : ''}`,
        skipped.length ? 'error' : 'success',
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
        actions={
          <Button variant="primary" icon={<Plus size={13} />} onClick={() => setCreateOpen(true)}>
            创建任务
          </Button>
        }
      />

      {/* 筛选一条：来源 / 范围 / 排序 / 仅可下载 / 搜索 / 刷新 / 条数 */}
      <div className="mt-3">
        <TaskFilterBar
          keyword={keywordInput}
          onKeywordChange={setKeywordInput}
          source={source}
          onSourceChange={v => patchFilter({ source: v === 'all' ? undefined : v })}
          canReadImages={canReadImages}
          scope={scope}
          onScopeChange={v => patchFilter({ scope: v === 'mine' ? undefined : v })}
          canReadAll={canReadAll}
          sort={sort}
          onSortChange={v => patchFilter({ sort: v === 'createdAt_desc' ? undefined : v })}
          onlyAuthorized={onlyAuthorized}
          onAuthorizedChange={v => patchFilter({ auth: v ? '1' : undefined })}
          onRefresh={() => void refetch()}
          isFetching={isFetching}
          total={isError ? undefined : data?.total}
        />
      </div>

      {data?.warnings.map(warning => (
        <p key={warning} role="alert" className="mt-3 text-[11.5px] text-danger">
          {warning}
        </p>
      ))}

      {/* 页签与批量条同一行：没勾选时右边是"从模板生成"的入口，勾选后就地换成批量操作，
          不再让四个不可用按钮常驻页头 */}
      <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
        <Tabs items={statusTabs} value={status} onChange={v => patchFilter({ status: v })} />
        {selected.size > 0 ? (
          <TaskBulkBar
            count={selected.size}
            busy={deleteTasks.isPending || deleteImages.isPending || updateTask.isPending}
            onSelectAll={toggleSelectAll}
            onClear={() => setSelected(new Set())}
            onDownload={handleDownload}
            onCollect={handleCollect}
            onDelete={handleDelete}
          />
        ) : (
          <Link
            to="/templates/library"
            className="text-[11.5px] text-ink-mute transition-colors hover:text-primary"
          >
            从模板生成 →
          </Link>
        )}
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
              <ImageTaskCard
                key={item.key}
                item={item.image}
                selected={selected.has(item.id)}
                onToggleSelect={toggleSelect}
                onOpen={openPreview}
              />
            ) : null)}
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
