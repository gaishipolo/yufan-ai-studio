import { CheckCircle2, Layers, RefreshCw } from 'lucide-react';
import { Link } from 'react-router-dom';
import { cn } from '@/shared/lib/cn';
import { useCan } from '@/shared/access/use-access';
import { Button, EmptyState } from '@/shared/ui';
import { Skeleton } from '@/shared/ui/EmptyState';
import { ErrorState } from '@/shared/ui/ErrorState';
import { STATUS, REVIEW_LABELS } from './replacementOptions';
import { replacementTaskFullName, replacementTaskName, replacementSourceName } from '@/entities/template/model/replacementTaskIdentity';
import { replacementElapsedText } from './replacementElapsed';
import type { ReplacementWorkbench } from './useReplacementWorkbench';
import { ReplacementRunManagement } from './ReplacementRunManagement';
import { ReplacementAccelerate } from './ReplacementAccelerate';

interface ReplacementTaskHistoryProps { model: ReplacementWorkbench }
export function ReplacementTaskHistory({ model }: ReplacementTaskHistoryProps) {
  const {catalog, setDetailId, historyTrash: trash, historyStatus: status, historySearch: search, setHistoryFilter} = model;
  /* 切换页签同时清空状态筛选，必须一次写完：两次 patch 会互相覆盖。 */
  const switchTab = (value: boolean) => setHistoryFilter({trash: value, status: ''});
  const setStatus = (value: string) => setHistoryFilter({status: value});
  const setSearch = (value: string) => setHistoryFilter({keyword: value});
  const runs = catalog.data?.runs ?? [];
  const current = runs.filter(run => !!run.deletedAt === trash);
  const filtered = current.filter(run => (!status || run.status === status) &&
    [replacementTaskName(run), run.id, run.taskId, replacementSourceName(run)]
      .some(value => value?.toLowerCase().includes(search.trim().toLowerCase())));
  /* 通用生成任务在任务中心，属于另一套体系（video_tasks + task:* 权限）；
     没有读取权限就不放跳转，避免送到必然 403 的页面。 */
  const canReadTasks = useCan('task:read');
  return (
    <section className="replacement-task-history rounded-xl border border-line bg-panel p-4">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <div className="min-w-0">
          <h2 className="font-semibold text-ink">替换任务与调用档案</h2>
          <p className="mt-0.5 text-[11.5px] text-ink-mute">替换档案、成片与人工验收；也可在任务中心统一跟踪进度</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {canReadTasks && <Link to="/tasks?source=replacement" className="text-xs text-primary hover:underline">任务中心 →</Link>}
          <Button size="sm" loading={catalog.isFetching} icon={<RefreshCw size={13} />} onClick={() => {void catalog.refetch();}}>刷新</Button>
        </div>
      </div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Button size="sm" variant={trash ? 'secondary' : 'primary'} onClick={() => switchTab(false)}>任务记录（{runs.filter(run => !run.deletedAt).length}）</Button>
        <Button size="sm" variant={trash ? 'primary' : 'secondary'} onClick={() => switchTab(true)}>回收站（{runs.filter(run => run.deletedAt).length}）</Button>
        <input aria-label="搜索替换任务" value={search} onChange={event => setSearch(event.target.value)} placeholder="搜索目标、原片或任务 ID" className="min-w-0 rounded-lg border border-line bg-panel px-3 py-2 text-xs text-ink" />
        <select aria-label="筛选替换任务状态" value={status} onChange={event => setStatus(event.target.value)} className="rounded-lg border border-line bg-panel px-3 py-2 text-xs text-ink"><option value="">全部状态</option>{[...new Set(current.map(run => run.status))].map(value => <option key={value} value={value}>{STATUS[value] ?? value}</option>)}</select>
      </div>
      {trash && <p className="mb-3 text-xs text-ink-soft">回收站保留完整调用档案和文件，可恢复；移入回收站不会取消正在执行的生成。</p>}
      {catalog.error && <ErrorState error={catalog.error} title={catalog.data ? '任务更新失败，以下为上次加载的记录' : '任务与素材包加载失败'} onRetry={() => {void catalog.refetch();}} />}
      {catalog.isLoading && !catalog.data ? <div role="status" aria-label="正在加载替换任务"><p className="replacement-operation-status">正在加载任务与素材包…</p><div className="space-y-3"><Skeleton className="h-14" /><Skeleton className="h-14" /><Skeleton className="h-14" /></div></div> :
      !filtered.length ? !catalog.error && <EmptyState icon={<Layers size={30} />} title={search || status ? '没有匹配的任务' : trash ? '回收站为空' : '还没有替换任务'} description={search || status ? '调整搜索关键词或状态筛选后重试。' : trash ? '移入回收站的任务可以在这里恢复。' : '选择原片与目标素材包，确认替换后即可开始。任务进度和生成结果会保存在这里。'} className="py-8 text-center" /> :
        <div className="space-y-2">{filtered.map(run => <article key={run.id} className="space-y-2 rounded-xl border border-line p-3"><button onClick={() => setDetailId(run.id)} className={cn('replacement-task-row flex w-full flex-wrap items-center gap-3 text-left hover:text-primary', run.status === 'success' && run.outputUrl && 'is-success')}>
          {run.status === 'success' && run.outputUrl && <CheckCircle2 size={17} className="text-ok" aria-hidden />}
          <span title={replacementTaskFullName(run)} className="break-words text-sm font-semibold text-ink">{replacementTaskName(run)}</span>
          {/* 列表里先给一个「这条跑的是封装提示词」的标记：不点开也能区分
              固定提示词任务和按素材现编的任务，两者提示词文本看不出差别。 */}
          {run.promptMode === 'fixed' && <span title={`固定提示词：${run.videoTemplateName ?? '已封装视频模板'}`} className="rounded-full bg-primary/10 px-2 py-0.5 text-[11px] text-primary">固定提示词</span>}
          <span className="ml-auto rounded-full bg-panel-soft px-3 py-1 text-xs text-ink-soft">{run.status === 'success' && !run.outputUrl ? '结果文件待恢复' : STATUS[run.status] ?? run.status}</span>
          {run.status === 'success' && <span className="text-xs text-ink-soft">人工验收：{REVIEW_LABELS[run.reviewStatus ?? 'pending'] ?? run.reviewStatus}</span>}
          <span className="text-xs text-primary">{run.status === 'success' && run.outputUrl ? '查看成片并下载 →' : '查看档案 →'}</span>
        </button><p title={replacementSourceName(run)} className="truncate text-xs text-ink-soft">原片：{replacementSourceName(run)}</p><p className="break-all text-[11px] text-ink-mute">创建时间：{new Date(run.createdAt).toLocaleString()} · 耗时：{replacementElapsedText(run)} · 任务编号：{run.id}{run.billingMode === 'credits' ? ' · 积分加速' : ''}</p><ReplacementAccelerate run={run} model={model} /><ReplacementRunManagement run={run} /></article>)}</div>}
    </section>
  );
}
