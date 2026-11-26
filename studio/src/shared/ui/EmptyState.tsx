import type { ReactNode } from 'react';
import { cn } from '@/shared/lib/cn';

export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: {
  icon?: ReactNode;
  title: string;
  /**
   * 只在**动作本身说不清**时才写。
   *
   * 空的这一屏是一次邀请，不是一段说明：把「去哪儿做」写进按钮里
   * （「去模板封装新建」），就别再在下面补一句「先在模板封装里定一张表
   * 并发布，它就会出现在这里供填表使用」——那句话把按钮又说了一遍。
   */
  description?: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn('flex flex-col items-center justify-center px-5 py-16 text-center', className)}>
      {icon && <div className="mb-3 text-ink-mute">{icon}</div>}
      <p className="font-serif text-lead font-semibold text-ink">{title}</p>
      {description && <p className="mt-1.5 max-w-sm text-meta text-ink-mute">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn('animate-pulse rounded-lg bg-line-soft', className)} />;
}

export function CardSkeletonGrid({ count = 8 }: { count?: number }) {
  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(196px,1fr))] gap-3.5">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="overflow-hidden rounded-card border border-line-soft">
          <Skeleton className="aspect-[4/4.6] rounded-none" />
          <div className="space-y-2 p-2.5">
            <Skeleton className="h-3 w-full" />
            <Skeleton className="h-3 w-2/3" />
          </div>
        </div>
      ))}
    </div>
  );
}
