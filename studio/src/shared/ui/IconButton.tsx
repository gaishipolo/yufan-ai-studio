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
        'grid h-[30px] w-[30px] place-items-center rounded-[9px] transition-colors',
        tone === 'default'
          ? 'text-ink-soft hover:bg-[#e8eff1] hover:text-ink'
          : 'bg-panel-soft text-ink-soft hover:bg-line-soft',
        className,
      )}
      {...rest}
    >
      {icon}
    </button>
  );
}
