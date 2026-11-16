import { ChartColumn, Clock, Image as ImageIcon, Info, ListChecks, Wallet } from 'lucide-react';
import { Chip, PageHeader } from '@/shared/ui';
import { cn } from '@/shared/lib/cn';

/**
 * 用量看板 · 骨架占位页
 *
 * 定位：只固定「看什么、怎么排」，不定义「数据从哪来」。
 * 后端接口就绪后，改动集中在 entities/usage（types / api / queries）+ 本文件的数据绑定，
 * 布局与路由不需要动。
 */

/** 汇总指标：只声明口径，不声明数据源 */
const METRICS = [
  { key: 'tasks', label: '生成任务', hint: '按状态汇总', icon: ListChecks },
  { key: 'duration', label: '视频时长', hint: '有效产出秒数', icon: Clock },
  { key: 'images', label: '图片张数', hint: '成功出图数', icon: ImageIcon },
  { key: 'cost', label: '预估费用', hint: '按模型单价折算', icon: Wallet },
];

/** 用量构成：口径待后端确认 */
const BREAKDOWN = ['文生视频', '图生视频', '生图', '音频'];

/** 建议契约：仅用于和后端对齐，未经实现 */
const ENDPOINTS = [
  { method: 'GET', path: '/usage/summary', desc: '汇总卡片' },
  { method: 'GET', path: '/usage/trend', desc: '按天趋势' },
  { method: 'GET', path: '/usage/breakdown', desc: '用量构成' },
];

export default function UsagePage() {
  return (
    <>
      <PageHeader
        title="用量看板"
        actions={<Chip tone="warn">接口未接入</Chip>}
      />

      {/* 明确告知读者：这里是骨架，不是「加载失败」。
          一句话说完就够 —— 标题旁已经有「接口未接入」的标记，
          下面再写一段「在哪儿补 types / api / queries」是写给开发看的，
          不是写给看这个页面的人看的。 */}
      <div className="mt-4 flex items-center gap-2 rounded-card border border-line-soft bg-panel-soft px-3 py-2.5">
        <Info size={14} className="shrink-0 text-ink-mute" />
        <p className="text-meta text-ink-soft">
          后端用量接口尚未实现，数值以 <span className="text-ink-mute">—</span> 占位。
        </p>
      </div>

      {/* 时间筛选：静态占位，接口接入后才启用交互，避免「点了没反应」 */}
      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        {['近 7 天', '近 30 天', '本月'].map((range, index) => (
          <span
            key={range}
            className={cn(
              'inline-flex h-[27px] items-center rounded-pill px-2.5 text-xs',
              index === 0 ? 'bg-panel font-semibold text-ink-soft shadow-card' : 'text-ink-mute',
            )}
          >
            {range}
          </span>
        ))}
        
      </div>

      {/* 汇总指标 */}
      <div className="mt-3 grid grid-cols-2 gap-2.5 md:grid-cols-4 md:gap-3.5">
        {METRICS.map((metric) => (
          <div key={metric.key} className="rounded-card border border-line-soft bg-panel p-3.5">
            <div className="flex items-center gap-1.5 text-meta text-ink-mute">
              <metric.icon size={13} />
              {metric.label}
            </div>
            <div className="mt-1.5 font-mono text-display font-bold leading-none tabular-nums text-ink-mute">—</div>
            <div className="mt-1.5 text-meta text-ink-mute">{metric.hint}</div>
          </div>
        ))}
      </div>

      <div className="mt-3 grid gap-3 md:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]">
        {/* 趋势图占位 */}
        <section className="rounded-card border border-line-soft bg-panel p-3.5">
          <header className="flex items-center justify-between gap-2">
            <h2 className="text-lead font-semibold text-ink">调用趋势</h2>
            <span className="text-meta text-ink-mute">按天</span>
          </header>
          <div className="mt-3 flex h-[176px] flex-col items-center justify-center gap-2 rounded-[10px] border border-dashed border-line-strong bg-panel-soft px-4 text-center">
            <ChartColumn size={26} strokeWidth={1.4} className="text-ink-mute" />
            <p className="text-xs text-ink-mute">图表占位</p>
            <p className="max-w-[260px] text-meta leading-[1.6] text-ink-mute">
              图表占位
            </p>
          </div>
        </section>

        {/* 构成占位 */}
        <section className="rounded-card border border-line-soft bg-panel p-3.5">
          <header className="flex items-center justify-between gap-2">
            <h2 className="text-lead font-semibold text-ink">用量构成</h2>
            <span className="text-meta text-ink-mute">占比</span>
          </header>
          <ul className="mt-3 space-y-2.5">
            {BREAKDOWN.map((label) => (
              <li key={label}>
                <div className="flex items-center justify-between text-meta text-ink-soft">
                  <span>{label}</span>
                  <span className="tabular-nums text-ink-mute">—</span>
                </div>
                <div className="mt-1 h-1.5 overflow-hidden rounded-pill bg-line-soft" />
              </li>
            ))}
          </ul>
        </section>
      </div>

      {/* 与后端对齐用的建议契约，不是已实现接口 */}
      <section className="mt-3 rounded-card border border-line-soft bg-panel p-3.5">
        <header className="flex items-center gap-2">
          <h2 className="text-lead font-semibold text-ink">接入清单</h2>
          <Chip>建议契约 · 待后端确认</Chip>
        </header>
        <ul className="mt-2.5 grid gap-1.5 text-meta md:grid-cols-2">
          {ENDPOINTS.map((endpoint) => (
            <li key={endpoint.path} className="flex items-center gap-2 text-ink-soft">
              <span className="rounded bg-panel-soft px-1.5 py-px font-mono text-micro text-primary-deep">
                {endpoint.method}
              </span>
              <span className="font-mono text-meta text-ink">{endpoint.path}</span>
              <span className="text-ink-mute">{endpoint.desc}</span>
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}
