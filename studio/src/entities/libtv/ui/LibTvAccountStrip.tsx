import { formatNumber } from '@/shared/lib/format';
import { cn } from '@/shared/lib/cn';
import type { LibTvAccount } from '../model/types';

/**
 * 账号额度条：可用积分、还能提交几个、0 积分权益什么时候到期。
 *
 * 为什么放在任务中心而不是「设置」：这三个数字解释的是**任务为什么慢/为什么开始扣钱**。
 * 权益到期那一刻，同一批任务的成本会从 0 变成每条 80~160 积分 —— 放在任务列表的
 * 上方，比放在三层菜单里的账单页更可能被看见。
 *
 * 拿不到账号数据时整条不渲染：这是增强信息，不该在任务中心里留下一块错误。
 */
export function LibTvAccountStrip({
  account,
  queue,
  className,
}: {
  account: LibTvAccount;
  /** 在跑的任务数（有排队时给一句"当前 N 个在队列里"） */
  queue?: { queued: number; limit: number } | null;
  className?: string;
}) {
  const asset = account.asset;
  if (!asset) return null;

  // 只显示最近到期的那一条：权益列表有 7 条，但它们的到期时间是同一个活动，
  // 全部铺开会把这条挤成两行。
  const expiring = account.zeroPowerBenefits
    ?.map(item => item.endTime)
    .filter(Boolean)
    .sort()[0];
  const endpoint = account.account.replace(/^https?:\/\//, '');

  return (
    <div
      className={cn(
        'mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-card border border-line-soft bg-panel',
        'px-3 py-2 text-micro text-ink-mute',
        className,
      )}
    >
      <span className="font-medium text-ink">LibTV · {asset.memberLevel || '账号'}</span>
      <span>
        可用{' '}
        <b className="font-mono tabular-nums text-ink">{formatNumber(asset.usablePower)}</b> 积分
      </span>
      <span>
        还能提交 <b className="font-mono tabular-nums text-ink">{asset.taskCanSubmit}</b> 个
      </span>
      {expiring ? (
        <span className="text-warn-deep">0 积分畅用至 {expiring.slice(0, 10)}</span>
      ) : null}
      {queue && queue.queued > 0 ? (
        <span className="text-warn-deep">
          本机 {queue.queued}/{queue.limit} 在 0 积分队列
        </span>
      ) : null}
      {/* 内网地址只在本机开发时露出来：演示给客户看时它是纯信息泄露 */}
      {import.meta.env.DEV ? (
        <span className="ml-auto font-mono text-ink-mute/70">{endpoint}</span>
      ) : null}
    </div>
  );
}
