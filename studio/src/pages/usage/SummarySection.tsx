import { MetricCard } from '@/entities/usage/ui/MetricCard';
import { StackedBar } from '@/entities/usage/ui/StackedBar';
import { formatDuration, formatNumber, formatPercent } from '@/entities/usage/model/format';
import type { UsageOverviewOut } from '@/entities/usage/model/types';

export interface SummarySectionProps {
  data: UsageOverviewOut;
}

/**
 * 概览的头部：一句话结论 + 一个比率。
 *
 * ## 为什么是「一大一小」而不是四张一样的卡
 *
 * 四张等大的卡读起来是「四个并列的事实」，用户得自己决定哪个重要。页面上真正
 * 的结论只有一个：**产出了多少**。所以给它最大的字号和三分之二的宽度，成功率
 * 独占一格；「发起次数」和「平均时长」降级成主卡底部的一行小字 —— 它们是分母
 * 和参照，不是结论。
 *
 * 层级靠**字号和位置**建立，不靠标签解释：哪张卡更大，眼睛就知道哪张是结论。
 */
export function SummarySection({ data }: SummarySectionProps) {
  const { summary } = data;

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {/* 主卡：产出了多少 */}
      <div className="rounded-card border border-line-soft bg-panel p-4 sm:col-span-2">
        <div className="flex flex-wrap items-start justify-between gap-x-8 gap-y-3">
          <div className="min-w-0">
            <p className="font-mono text-micro tracking-[.1em] text-ink-mute">已产出</p>
            <p className="mt-1.5 flex items-baseline gap-1.5">
              {/* 这一页只有一个结论，所以只有这一个数字给到 34px：
                  扫一眼就知道产出了多少，其余数字都退到参照的位置。 */}
              <span className="font-mono text-stat font-semibold leading-none tabular-nums text-ink">
                {formatNumber(summary.quantity)}
              </span>
              <span className="text-body text-ink-mute">件</span>
            </p>
          </div>
          <div className="sm:text-right">
            <p className="font-mono text-micro tracking-[.1em] text-ink-mute">视频时长</p>
            <p className="mt-1.5 font-mono text-lead font-semibold tabular-nums text-ink">
              {formatDuration(summary.durationSec)}
            </p>
          </div>
        </div>

        <div className="mt-4">
          <StackedBar
            ariaLabel={`发起 ${summary.total} 次：成功 ${summary.succeeded}、失败 ${summary.failed}、进行中 ${summary.pending}`}
            segments={[
              { key: 'succeeded', value: summary.succeeded, tone: 'primary', label: '已产出' },
              { key: 'failed', value: summary.failed, tone: 'danger', label: '失败' },
              { key: 'pending', value: summary.pending, tone: 'mute', label: '进行中' },
            ]}
          />
          <div className="mt-2 flex flex-wrap items-center justify-between gap-x-4 gap-y-1 text-meta text-ink-mute">
            <span className="font-mono tabular-nums">发起 {formatNumber(summary.total)} 次</span>
            <span className="font-mono tabular-nums">
              {summary.durationSec > 0 ? `平均 ${formatDuration(summary.avgDurationSec)}/条` : '暂无成功产出'}
            </span>
          </div>
        </div>
      </div>

      {/* 比率卡：成不成功。
          刻意**不**用 hero 字号：34px 是留给左边那个结论的，
          两个 34px 并排就没有结论可言了 —— 眼睛该先落在「产出了多少」上。 */}
      <MetricCard
        className="sm:col-span-2 xl:col-span-1"
        label="成功率"
        value={formatPercent(summary.successRate)}
        progress={summary.successRate}
        hint={
          <span className="font-mono tabular-nums">
            成功 {formatNumber(summary.succeeded)} · 失败 {formatNumber(summary.failed)}
          </span>
        }
      />
    </div>
  );
}
