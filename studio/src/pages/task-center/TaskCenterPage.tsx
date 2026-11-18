import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { FolderOpen } from 'lucide-react';
import {
  CardSkeletonGrid,
  ConfirmDialog,
  EmptyState,
  ErrorState,
  PageHeader,
  Pager,
  Tabs,
  type TabItem,
} from '@/shared/ui';
import { toast } from '@/shared/lib/toast';
import { downloadUrl } from '@/shared/lib/download';
import { useCan } from '@/shared/access/use-access';
import { useDebouncedValue } from '@/shared/hooks/useDebouncedValue';
import { TaskCard } from '@/entities/task/ui/TaskCard';
import { ImageTaskCard } from '@/entities/task/ui/ImageTaskCard';
import { LibTvAccountStrip } from '@/entities/libtv/ui/LibTvAccountStrip';
import { useLibtvAccount, useLibtvQueue } from '@/entities/libtv/api/libtv.queries';
import { isQueued, queueLabel, upstreamTaskId } from '@/entities/libtv/model/types';
import { useDeleteImages, useDeleteTasks, useTaskFeed, useUpdateTask } from '@/entities/task/api/task.queries';
import type { TaskScope, TaskSort } from '@/entities/task/model/types';
import type { TaskFeedItem, TaskFeedParams, TaskFeedSource, TaskFeedStatus } from '@/entities/task/model/feed.types';
import { TaskFilterBar } from '@/features/task-filter/ui/TaskFilterBar';
import { TaskBulkBar } from '@/features/task-filter/ui/TaskBulkBar';
import { TaskPreviewModal } from '@/features/task-preview/TaskPreviewModal';
import { ImagePreviewModal } from '@/features/task-preview/ImagePreviewModal';
import { generatedFileName } from '@/shared/lib/format';
import { useSessionStore } from '@/shared/access/session';

type StatusFilter = TaskFeedStatus;

/** 队列位次只对这一个模型有意义（上游是 LibTV 的画布队列） */
const LIBTV_MODEL_ID = 'libtv-wan3-replacement';

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
  /* 批量删除是**硬删**（后端 `session.delete`），没有回收站可退。确认框不是
     客套：这一排按钮挨着「全选 / 收藏」，误点的成本是不可恢复的。 */
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [preview, setPreview] = useState<TaskFeedItem | null>(null);
  const canReadImages = useCan('image:read');
  // 写操作按权限藏起来：只读角色点到只会拿到 403（2026-11-15 审查 3.1）
  const canCollect = useCan('task:update');
  // 两个 hook 都要调用（`||` 短路会让第二个变成条件调用），再取或
  const canDeleteTask = useCan('task:delete');
  const canDeleteImage = useCan('image:delete');
  const canDelete = canDeleteTask || canDeleteImage;
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

  /** 有没有筛选生效（不算状态页签 —— 那是浏览位置的切换，不是筛选）。 */
  const hasFilter =
    keyword.trim() !== '' ||
    source !== 'all' ||
    scope !== 'mine' ||
    sort !== 'createdAt_desc' ||
    onlyAuthorized;

  /** 一键清空筛选：搜索框是本地 state，要一起清。 */
  const resetFilter = useCallback(() => {
    setKeywordInput('');
    setSelected(new Set());
    patchParams({
      keyword: undefined,
      source: undefined,
      scope: undefined,
      sort: undefined,
      auth: undefined,
      page: undefined,
    });
  }, [patchParams]);

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

  /* ---------- LibTV 队列：把「卡在 48%」和「排队第 3 位」分开 ----------
     只有跑着的视频任务才需要问上游；一条批量请求问完这一页，别让 48 张卡各发一次。
     `externalId` 形如 `<归档工作区>:<上游 task_id>`，排队接口只认后面那半段。 */
  const canReadQueue = useCan('task:read');
  const canReadLibtvAccount = useCan('model_driver:read');
  const upstreamIds = useMemo(
    () =>
      items.flatMap(item => {
        if (item.source !== 'video' || item.group !== 'running') return [];
        // 只有 LibTV 的任务号能在那个接口上查：百炼的 externalId 是自己家的 UUID，
        // 拿去问 LibTV 只会得到空结果，白打一次上游。
        if (item.video?.modelId !== LIBTV_MODEL_ID) return [];
        const id = upstreamTaskId(item.video?.externalId);
        return id ? [id] : [];
      }),
    [items],
  );
  // 没权限就别发请求：排队位次要 `task:read`，额度条要 `model_driver:read`。
  // 以前不论有没有都发，普通角色每分钟收一个 403，还被 throwOnError 吞掉。
  const { data: queueData } = useLibtvQueue(upstreamIds, canReadQueue);
  const queueByTask = useMemo(
    () => new Map((queueData?.items ?? []).map(item => [item.taskId, item])),
    [queueData?.items],
  );
  const queuedCount = useMemo(
    () => (queueData?.items ?? []).filter(isQueued).length,
    [queueData?.items],
  );
  const { data: libtvAccount } = useLibtvAccount(canReadLibtvAccount);

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

  const requestDelete = () => {
    if (!selected.size) return toast('请先勾选要删除的任务', 'error');
    setConfirmDelete(true);
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
          filtered={hasFilter}
          onReset={resetFilter}
        />
      </div>

      {data?.warnings.map(warning => (
        <p key={warning} role="alert" className="mt-3 text-meta text-danger">
          {warning}
        </p>
      ))}

      {libtvAccount ? (
        <LibTvAccountStrip
          account={libtvAccount}
          queue={{ queued: queuedCount, limit: queueData?.items[0]?.queueLimit ?? 10 }}
        />
      ) : null}

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
            onDelete={requestDelete}
            canCollect={canCollect}
            canDelete={canDelete}
          />
        ) : (
          <Link
            to="/templates/library"
            className="text-meta text-ink-mute transition-colors hover:text-primary"
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
            /* 空态不承诺不存在的能力：这里**有**「重置筛选」按钮了
               （有筛选生效时才出现），所以指引就是那个按钮本身。 */
            description={hasFilter ? '当前筛选条件下没有任务' : '还没有任务'}
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
                queueHint={queueLabel(queueByTask.get(upstreamTaskId(item.video.externalId) ?? '') ?? null)}
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

      <ConfirmDialog
        open={confirmDelete}
        title="删除选中的任务？"
        description="删除后任务记录与其产出链接都会消失，无法恢复。"
        details={[
          {label: '总计', value: `${selected.size} 个`},
          {
            label: '视频',
            value: `${[...selected].filter(id => sourceOf.get(id) === 'video').length} 个`,
          },
          {
            label: '生图',
            value: `${[...selected].filter(id => sourceOf.get(id) === 'image').length} 个`,
          },
        ]}
        notice="已归档的成片字节不会被回收。"
        noticeTone="danger"
        confirmLabel="确认删除"
        busy={deleteTasks.isPending || deleteImages.isPending}
        onCancel={() => setConfirmDelete(false)}
        onConfirm={() => {
          setConfirmDelete(false);
          void handleDelete();
        }}
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
    </>
  );
}
