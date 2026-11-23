import { useCallback, useEffect, useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useCan } from '@/shared/access/use-access';
import { ErrorState, Notice, PageHeader, Segmented, Tabs, type TabItem } from '@/shared/ui';
import { useUsageOverview, useUsageRecords } from '@/entities/usage/api/usage.queries';
import type { UsageGranularity, UsageScope } from '@/entities/usage/model/types';
import { OverviewSection } from './OverviewSection';
import { RecordsSection } from './RecordsSection';
import {
  GRANULARITY_OPTIONS,
  RANGE_OPTIONS,
  SCOPE_OPTIONS,
  rangeBounds,
  type RangeKey,
} from './range';

/** 明细每页条数。后端上限 100，这里取 20 —— 一屏能扫完，又不至于一次拉太多。 */
const PAGE_SIZE = 20;

type TabKey = 'overview' | 'records';

/**
 * 用量看板。
 *
 * ## 页面为什么分成「概览 / 明细」两页签
 *
 * 两者的读者不是同一个人：管理者看**概览**（这段时间生成了多少、趋势如何、
 * 谁用得最多），执行的人看**明细**（我昨天那条成片到底有没有算进去）。
 * 混在一屏里，明细的表格会把上方的指标卡挤到折叠线以下 —— 首屏变成一张
 * 要滚的表，趋势图反而没人看。
 *
 * ## 筛选条件全部挂在 URL 上
 *
 * 与任务中心同一条规矩：可分享、可回退、刷新不丢。看板尤其需要 —— 「你上
 * 个月 30 天、按周、全部成员的那张图」是要被贴到群里的。
 */
export default function UsagePage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const canReadAll = useCan('usage:read:all');

  /* ---------- 从 URL 读筛选条件 ---------- */
  const rangeParam = searchParams.get('range');
  const range: RangeKey = RANGE_OPTIONS.some((option) => option.value === rangeParam)
    ? (rangeParam as RangeKey)
    : '30d';
  const granularityParam = searchParams.get('granularity');
  const granularity: UsageGranularity = granularityParam === 'week' ? 'week' : 'day';
  const scopeParam = searchParams.get('scope');
  const requestedScope: UsageScope = scopeParam === 'all' ? 'all' : 'mine';
  /* 没有 `usage:read:all` 时 URL 里可能残留 scope=all（手改链接、或者权限被
     回收）。后端会把越权请求降级成 mine，界面也必须显示 mine —— 否则用户看着
     「全部成员」，数据其实只有自己的，这个不一致比不给看更糟。 */
  const scope: UsageScope = requestedScope === 'all' && canReadAll ? 'all' : 'mine';
  const tab: TabKey = searchParams.get('tab') === 'records' ? 'records' : 'overview';
  const page = Math.max(1, Math.floor(Number(searchParams.get('page') ?? '1') || 1));

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

  /* 权限被回收时把 URL 里的 scope=all 清掉：留着一个点不动的筛选项，
     用户会以为筛选坏了。 */
  useEffect(() => {
    if (requestedScope === 'all' && !canReadAll) patchParams({ scope: undefined });
  }, [requestedScope, canReadAll, patchParams]);

  /* 改筛选条件就回到第 1 页：否则会出现「第 3 页但没有结果」。 */
  const patchFilter = useCallback(
    (patch: Record<string, string | undefined>) => {
      patchParams({ ...patch, page: undefined });
    },
    [patchParams],
  );

  /* 区间按 UTC 算，和后端的桶边界一致（图里的今天 = 列表里的今天）。 */
  const bounds = useMemo(() => rangeBounds(range), [range]);

  const overviewParams = useMemo(
    () => ({ start: bounds.start, end: bounds.end, granularity, scope }),
    [bounds.start, bounds.end, granularity, scope],
  );
  const recordsParams = useMemo(
    () => ({ start: bounds.start, end: bounds.end, scope, page, pageSize: PAGE_SIZE }),
    [bounds.start, bounds.end, scope, page],
  );

  const overview = useUsageOverview(overviewParams, tab === 'overview');
  const records = useUsageRecords(recordsParams, tab === 'records');

  const tabItems: TabItem<TabKey>[] = [
    { value: 'overview', label: '概览' },
    { value: 'records', label: '明细' },
  ];

  const rangeLabel = RANGE_OPTIONS.find((option) => option.value === range)?.label ?? '近 30 天';
  const scopeLabel = scope === 'all' ? '全部成员' : '我的';
  const totalPages = records.data ? Math.max(1, Math.ceil(records.data.total / PAGE_SIZE)) : 0;

  return (
    <>
      <PageHeader
        title="用量看板"
        meta={
          <span className="flex flex-wrap items-center gap-x-2">
            <span>{rangeLabel}</span>
            <span>·</span>
            <span>{scopeLabel}</span>
            <span>·</span>
            <span className="font-mono tabular-nums">
              {bounds.start} → {bounds.end} UTC
            </span>
          </span>
        }
        actions={
          <Tabs
            items={tabItems}
            value={tab}
            onChange={(value) => patchFilter({ tab: value === 'overview' ? undefined : value })}
          />
        }
      />

      {/* 筛选条：三组互斥选项，各管一件事。粒度只影响趋势图，所以只在概览页签下显示 —— */}
      <div className="mt-3.5 flex flex-wrap items-center gap-x-5 gap-y-2.5">
        <Segmented
          className="max-sm:w-full"
          options={RANGE_OPTIONS}
          value={range}
          onChange={(value) => patchFilter({ range: value === '30d' ? undefined : value })}
          aria-label="时间区间"
        />
        {tab === 'overview' && (
          <Segmented
            className="max-sm:w-full"
            options={GRANULARITY_OPTIONS}
            value={granularity}
            onChange={(value) => patchFilter({ granularity: value === 'day' ? undefined : value })}
            aria-label="趋势粒度"
          />
        )}
        <Segmented
          /* 三组控件从左到右排开，不用 auto margin 把它推到右侧：
             auto margin 在可换行的 flex 行里，「贴右」和「换行」是两件事 ——
             窗口宽度一变，它就会跳到下一行去。 */
          className="max-sm:w-full"
          options={SCOPE_OPTIONS}
          value={scope}
          onChange={(value) => patchFilter({ scope: value === 'mine' ? undefined : value })}
          disabledValues={canReadAll ? undefined : ['all']}
          hints={{
            all: canReadAll ? '包含所有成员的用量' : '需要「查看全部用量」权限',
            mine: '只看自己的用量',
          }}
          aria-label="数据范围"
        />
      </div>

      {tab === 'overview' ? (
        <div className="mt-3.5">
          {overview.isLoading ? (
            /* 骨架按真实布局摆：先一大一小两张卡，再一块趋势区。形状对上了，
               数据一填进来页面不会「跳」一下。 */
            <div className="space-y-3">
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
                <div className="h-[148px] animate-pulse rounded-card bg-panel-soft sm:col-span-2" />
                <div className="h-[148px] animate-pulse rounded-card bg-panel-soft" />
              </div>
              <div className="h-[236px] animate-pulse rounded-card bg-panel-soft" />
            </div>
          ) : overview.isError ? (
            <ErrorState
              error={overview.error}
              title="用量加载失败"
              onRetry={() => void overview.refetch()}
              retrying={overview.isFetching}
            />
          ) : overview.data ? (
            <>
              {/* 没有接上的数据来源要**说清楚**：这时候页面全是 0，
                  用户会以为是「这个月没生成过」，而不是「看板没接数据源」。 */}
              {overview.data.sources.length === 0 && (
                <Notice tone="warning" className="mb-3">
                  暂无可用的数据来源，看板显示为中性值。请确认用量来源是否已注册。
                </Notice>
              )}
              <OverviewSection data={overview.data} granularity={granularity} />
            </>
          ) : null}
        </div>
      ) : (
        <div className="mt-3.5">
          <RecordsSection
            records={records.data?.items ?? []}
            total={records.data?.total ?? 0}
            page={page}
            totalPages={totalPages}
            isLoading={records.isLoading}
            isFetching={records.isFetching}
            error={records.error}
            onRetry={() => void records.refetch()}
            onPageChange={(next) => patchParams({ page: next === 1 ? undefined : String(next) })}
          />
        </div>
      )}
    </>
  );
}
