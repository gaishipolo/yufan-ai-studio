import { Link } from 'react-router-dom';
import { Inbox } from 'lucide-react';
import { Chip, EmptyState, ErrorState, Panel, PanelHeader, Pager, Skeleton } from '@/shared/ui';
import { sourceLabel, statusLabel, statusTone } from '@/entities/usage/model/source-labels';
import { formatDateTime, formatDuration, formatNumber } from '@/entities/usage/model/format';
import type { UsageRecordOut } from '@/entities/usage/model/types';

/** 状态词 → Chip 的色调。`statusTone` 的 'bad' / 'mute' 在 Chip 里没有同名档。 */
const TONE_MAP = {
  ok: 'ok',
  brand: 'brand',
  bad: 'danger',
  mute: 'default',
} as const;

export interface RecordsSectionProps {
  records: UsageRecordOut[];
  /** 全量条数（不是当前页条数）——标题要显示全量，否则用户以为翻页丢了数据 */
  total: number;
  page: number;
  totalPages: number;
  isLoading: boolean;
  isFetching: boolean;
  error: unknown;
  onRetry: () => void;
  onPageChange: (page: number) => void;
}

/**
 * 用量明细：一条生成记录一行。
 *
 * 为什么用表格而不是卡片：明细是**对账**用的 —— 用户想知道「哪一条、什么时候、
 * 出了几个、多长」。卡片会把每一行摊成一大块，扫一列数字（时长、次数）要
 * 竖着滚一屏；表格让同一列对齐，才扫得动。
 *
 * 时间一律 UTC（见 `entities/usage/model/format.ts` 的理由）：这一列和上方
 * 趋势图的桶必须是同一个时区，否则同一件事在两处会落在不同日期上。
 */
export function RecordsSection({
  records,
  total,
  page,
  totalPages,
  isLoading,
  isFetching,
  error,
  onRetry,
  onPageChange,
}: RecordsSectionProps) {
  return (
    <Panel>
      <PanelHeader title="用量明细" meta={isLoading ? '加载中' : `共 ${formatNumber(total)} 条`} />

      {isLoading ? (
        <div className="space-y-1.5">
          {Array.from({ length: 6 }).map((_, index) => (
            <Skeleton key={index} className="h-8 w-full" />
          ))}
        </div>
      ) : error ? (
        <ErrorState error={error} title="用量明细加载失败" onRetry={onRetry} retrying={isFetching} />
      ) : records.length === 0 ? (
        <EmptyState icon={<Inbox size={38} strokeWidth={1.4} />} title="这个区间没有用量记录" />
      ) : (
        <>
          {/* 窄屏放不下 8 列，给一句「能滑」的提示 —— 否则用户只会以为右边被截断了。 */}
          <p className="mb-1.5 text-micro text-ink-mute sm:hidden">左右滑动查看完整列</p>
          <div className="-mx-3.5 overflow-x-auto">
            <table className="w-full min-w-[860px] border-collapse text-left">
              <thead>
                <tr className="font-mono text-micro tracking-[.1em] text-ink-soft">
                  <th className="border-b-2 border-primary px-3.5 py-2 font-semibold">时间（UTC）</th>
                  <th className="border-b-2 border-primary px-3.5 py-2 font-semibold">来源</th>
                  <th className="border-b-2 border-primary px-3.5 py-2 font-semibold">标题</th>
                  <th className="border-b-2 border-primary px-3.5 py-2 font-semibold">模型</th>
                  <th className="border-b-2 border-primary px-3.5 py-2 text-right font-semibold">数量</th>
                  <th className="border-b-2 border-primary px-3.5 py-2 text-right font-semibold">时长</th>
                  <th className="border-b-2 border-primary px-3.5 py-2 font-semibold">状态</th>
                  <th className="border-b-2 border-primary px-3.5 py-2 font-semibold">使用人</th>
                </tr>
              </thead>
              <tbody>
                {records.map((record) => (
                  <tr
                    key={`${record.source}:${record.id}`}
                    className="border-b border-line-soft transition-colors last:border-0 hover:bg-panel-soft"
                  >
                    <td className="whitespace-nowrap px-3.5 py-2 font-mono text-meta tabular-nums text-ink-soft">
                      {formatDateTime(record.createdAt)}
                    </td>
                    <td className="whitespace-nowrap px-3.5 py-2 text-xs text-ink-soft">{sourceLabel(record.source)}</td>
                    <td className="max-w-[280px] px-3.5 py-2 text-xs text-ink">
                      <span className="block truncate" title={record.title}>
                        {record.detailUrl ? (
                          <Link
                            to={record.detailUrl}
                            className="transition-colors hover:text-primary-deep hover:underline"
                          >
                            {record.title || record.id}
                          </Link>
                        ) : (
                          record.title || record.id
                        )}
                      </span>
                    </td>
                    <td className="whitespace-nowrap px-3.5 py-2 text-xs text-ink-soft">{record.modelName || '—'}</td>
                    <td className="px-3.5 py-2 text-right font-mono text-xs tabular-nums text-ink-soft">
                      {formatNumber(record.quantity)}
                    </td>
                    <td className="px-3.5 py-2 text-right font-mono text-xs tabular-nums text-ink-soft">
                      {record.durationSec > 0 ? formatDuration(record.durationSec) : '—'}
                    </td>
                    <td className="px-3.5 py-2">
                      <Chip tone={TONE_MAP[statusTone(record.status)]}>{statusLabel(record.status)}</Chip>
                    </td>
                    <td className="whitespace-nowrap px-3.5 py-2 text-xs text-ink-soft">{record.ownerName || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <Pager
            page={page}
            totalPages={totalPages}
            busy={isFetching}
            onChange={onPageChange}
            className="-mx-3.5 mt-1"
          />
        </>
      )}
    </Panel>
  );
}
