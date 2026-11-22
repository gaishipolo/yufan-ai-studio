import { useMemo, useState } from 'react';
import { TrendChart } from '@/entities/usage/ui/TrendChart';
import { BreakdownBars } from '@/entities/usage/ui/BreakdownBars';
import type {
  UsageDimension,
  UsageGranularity,
  UsageOverviewOut,
} from '@/entities/usage/model/types';
import { EmptyState, Panel, PanelHeader, Segmented } from '@/shared/ui';
import { SummarySection } from './SummarySection';

const DIMENSION_OPTIONS: { value: UsageDimension; label: string }[] = [
  { value: 'model', label: '按模型' },
  { value: 'kind', label: '按方式' },
  { value: 'owner', label: '按使用人' },
  { value: 'status', label: '按状态' },
];

export interface OverviewSectionProps {
  data: UsageOverviewOut;
  granularity: UsageGranularity;
}

/**
 * 概览：结论 + 趋势 + 构成。
 *
 * 三块的顺序是按「先结论、再过程、再拆解」排的：先回答「生成了多少」，再回答
 * 「什么时候生成的」，最后回答「是谁、用什么生成的」。
 *
 * 趋势单独占一整行：它是这一页最该被看的东西，压到半宽就只剩一根根细柱。
 */
export function OverviewSection({ data, granularity }: OverviewSectionProps) {
  const [dimension, setDimension] = useState<UsageDimension>('model');

  const breakdown = useMemo(
    () => data.breakdown.filter((row) => row.dimension === dimension).slice(0, 8),
    [data.breakdown, dimension],
  );

  return (
    <div className="space-y-3">
      <SummarySection data={data} />

      <Panel>
        <PanelHeader
          title="生成趋势"
          meta={`${data.trend.length} 个${granularity === 'week' ? '周' : '日'}桶`}
        />
        {data.trend.length > 0 ? (
          <TrendChart points={data.trend} granularity={granularity} />
        ) : (
          <EmptyState title="没有趋势数据" description="换一个区间试试" />
        )}
      </Panel>

      <Panel>
        <PanelHeader
          title="用量构成"
          meta={`${breakdown.length} 项`}
          stackActions
          actions={
            <Segmented
              className="max-sm:w-full"
              options={DIMENSION_OPTIONS}
              value={dimension}
              onChange={setDimension}
              aria-label="构成维度"
            />
          }
        />
        <BreakdownBars items={breakdown} />
      </Panel>
    </div>
  );
}
