import { useEffect, useRef } from 'react';
import type { UsageTrendPointOut } from '../model/types';
import { cn } from '@/shared/lib/cn';

export interface TrendChartProps {
  points: UsageTrendPointOut[];
  /** X 轴标签是「日」还是「周」 */
  granularity: 'day' | 'week';
  className?: string;
}

/**
 * 趋势柱状图（堆叠）。
 *
 * 为什么自己画而不是引图表库：这个看板只有「按天的三段堆叠柱」这一种图，
 * 为它引入 recharts 之类的依赖要多背一份包体和一个版本升级的活。柱子的比例
 * 用 flex + 百分比高度实现，浏览器自己算。
 *
 * 三段从下到上：已产出（主色蓝）/ 失败（红）/ 进行中（灰）。堆叠而不是分组 ——
 * 分组的话「这一天一共发起了多少次」要靠眼睛把三根柱加起来，堆叠一眼就是总数。
 *
 * ## 三个刻意的取舍
 *
 * - **颜色只用设计令牌**：主色 / 危险色 / 中性线，三段与卡片里的比例条、构成条
 *   完全一致 —— 同一个页面里「已产出」不能有两种颜色。
 * - **只标一个峰值**：标满每根柱子就成了一片数字；标最高的那一个，读者一眼
 *   知道「峰值在哪、多高」，其余的靠高度自己比。
 * - **悬停出浮层而不是浏览器的 title**：原生 title 要等一秒、样式也不受控，
 *   而且和图例挤在一起。这里自己画一个，位置贴着柱顶。
 */
export function TrendChart({ points, granularity, className }: TrendChartProps) {
  const max = points.reduce((acc, point) => Math.max(acc, point.total), 0);
  const scale = (value: number) => (max > 0 ? (value / max) * 100 : 0);

  const scrollerRef = useRef<HTMLDivElement>(null);

  /* 窄屏横向滚动时，默认停在**最右**：时间轴的右端才是「最近」，
     而最近的数据才是用户进这一页要看的。停在左边等于让他先划过一片空白。 */
  useEffect(() => {
    const el = scrollerRef.current;
    if (el && el.scrollWidth > el.clientWidth) el.scrollLeft = el.scrollWidth;
  }, [points]);

  // 峰值桶：第一个达到最大值的桶。值相同就标第一个，避免一排重复的数字。
  const peakIndex = points.findIndex((point) => point.total === max);

  // X 轴标签太多会挤成一团：均匀取 5 个（首尾一定保留）
  const labelStep = Math.max(1, Math.ceil(points.length / 5));
  const shownLabels = points
    .map((point, index) => ({ point, index }))
    .filter(({ index }) => index % labelStep === 0 || index === points.length - 1)
    .map(({ point, index }) => ({ point, isLast: index === points.length - 1 }));

  return (
    <div className={cn('w-full', className)}>
      {/* 窄屏放不下 30 根柱子时横向滑动，而不是把柱子压成看不见的一条线。 */}
      <div ref={scrollerRef} className="overflow-x-auto">
        <div className="min-w-[420px]">
          <div
            /* `pt-4` 是留给峰值标注的：最高的柱子是满高的，它的数字会标在柱顶
               之上，不留这 16px 就会顶到面板标题上。 */
            className="flex h-44 items-end gap-[3px] border-b border-line-soft pt-4"
            role="group"
            aria-label={`生成量趋势图（按${granularity === 'week' ? '周' : '日'}）`}
          >
            {points.map((point, index) => {
              const total = point.total;
              const share = (value: number) => (total > 0 ? (value / total) * 100 : 0);
              const tooltipAlign =
                index === 0
                  ? 'left-0 translate-x-0'
                  : index === points.length - 1
                    ? 'right-0 left-auto translate-x-0'
                    : 'left-1/2 -translate-x-1/2';
              return (
                <div
                  key={point.bucket}
                  className="group relative flex h-full flex-1 flex-col justify-end"
                  role="img"
                  aria-label={`${point.bucket}：发起 ${point.total} 次，产出 ${point.succeeded} 次，失败 ${point.failed} 次，进行中 ${point.pending} 次`}
                >
                  <div
                    className="relative flex w-full flex-col-reverse"
                    style={{ height: `${scale(total)}%` }}
                  >
                    <span className="w-full bg-primary transition-colors duration-150 group-hover:bg-primary-deep" style={{ height: `${share(point.succeeded)}%` }} />
                    <span className="w-full bg-danger/70 transition-colors duration-150 group-hover:bg-danger" style={{ height: `${share(point.failed)}%` }} />
                    <span className="w-full bg-line-strong transition-colors duration-150 group-hover:bg-line" style={{ height: `${share(point.pending)}%` }} />

                    {/* 峰值标注：只标一个 */}
                    {index === peakIndex && max > 0 && (
                      <span className="absolute inset-x-0 -top-4 text-center font-mono text-micro tabular-nums text-ink-soft">
                        {point.total}
                      </span>
                    )}

                    {/* 悬停浮层：贴着柱顶。触屏没有 hover，直接不显示（`hover:none`）。 */}
                    <span
                      className={cn(
                        'pointer-events-none absolute bottom-full z-10 mb-1.5 hidden w-max max-w-[13rem] flex-col gap-0.5 rounded-lg bg-ink px-2.5 py-1.5 text-micro leading-relaxed text-white shadow-float group-hover:flex [@media(hover:none)]:hidden',
                        tooltipAlign,
                      )}
                    >
                      <span className="font-mono tabular-nums text-white/70">{point.bucket}</span>
                      <span className="whitespace-nowrap">
                        发起 {point.total} · 产出 {point.succeeded} · 失败 {point.failed} · 进行中{' '}
                        {point.pending}
                      </span>
                    </span>
                  </div>
                </div>
              );
            })}
          </div>

          {/* 标签只给「要显示的那几个」，两端对齐、中间均分。
              每个桶都放一个标签的位置是不够宽的：桶宽 13px，「10-24」要 28px，
              于是每个标签都被截成「1…」。这里让标签自己占位，宽度就回来了。 */}
          <div className="mt-1.5 flex justify-between">
            {shownLabels.map(({ point, isLast }) => (
              <span
                key={point.bucket}
                className={cn(
                  'font-mono text-micro tabular-nums text-ink-mute',
                  isLast ? 'text-right' : 'text-center',
                )}
              >
                {point.bucket.slice(5)}
              </span>
            ))}
          </div>
        </div>
      </div>

      <div className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-meta text-ink-mute">
        <span className="inline-flex items-center gap-1.5">
          <i className="inline-block h-2 w-2 rounded-xs bg-primary" />已产出
        </span>
        <span className="inline-flex items-center gap-1.5">
          <i className="inline-block h-2 w-2 rounded-xs bg-danger/70" />失败
        </span>
        <span className="inline-flex items-center gap-1.5">
          <i className="inline-block h-2 w-2 rounded-xs bg-line-strong" />进行中
        </span>
      </div>
    </div>
  );
}
