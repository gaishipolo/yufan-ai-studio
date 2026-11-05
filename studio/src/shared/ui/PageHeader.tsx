import type { ReactNode } from 'react';
import { cn } from '@/shared/lib/cn';

export interface PageHeaderProps {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  className?: string;
}

export function PageHeader({ title, description, actions, className }: PageHeaderProps) {
  return (
    <div className={cn('flex flex-col gap-3 md:flex-row md:items-start md:justify-between', className)}>
      <div className="min-w-0">
        <h1 className="mb-1 mt-0.5 text-[19px] font-bold tracking-[.2px] text-ink">{title}</h1>
        {description && (
          <p className="max-w-[300px] text-[11.5px] leading-[1.55] text-ink-mute md:max-w-[440px] md:text-xs">{description}</p>
        )}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-1.5 md:justify-end">{actions}</div>}
    </div>
  );
}
