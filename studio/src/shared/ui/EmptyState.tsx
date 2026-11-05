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
  description?: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn('flex flex-col items-center justify-center px-5 py-16 text-center', className)}>
      {icon && <div className="mb-3 text-[#ccd9de]">{icon}</div>}
      <p className="text-[13px] font-medium text-ink-soft">{title}</p>
      {description && <p className="mt-1.5 max-w-sm text-xs text-ink-mute">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn('animate-pulse rounded-lg bg-[#eef3f5]', className)} />;
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
