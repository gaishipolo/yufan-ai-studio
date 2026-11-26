import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { cn } from '@/shared/lib/cn';

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  icon: ReactNode;
  label: string;
  tone?: 'default' | 'soft';
}

export function IconButton({ icon, label, tone = 'default', className, ...rest }: IconButtonProps) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className={cn(
        /* 方形微圆：纸感里的工具按钮是「一枚铅字块」，
           不是药丸也不是圆点。 */
        'grid h-[30px] w-[30px] place-items-center rounded-md transition-colors',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30',
        tone === 'default'
          ? 'text-ink-soft hover:bg-panel-soft hover:text-ink'
          : 'bg-panel-soft text-ink-soft hover:bg-line-soft',
        className,
      )}
      {...rest}
    >
      {icon}
    </button>
  );
}
