import type { ReactNode } from 'react';
import { cn } from '@/shared/lib/cn';

export type ChipTone = 'default' | 'primary' | 'ok' | 'warn' | 'danger';

/* 只用 token：原来这里写死了 10 个十六进制值，换主题时只有它不跟着变。 */
const TONES: Record<ChipTone, string> = {
  default: 'bg-panel-soft text-ink-soft border-line-soft',
  primary: 'bg-primary-soft text-primary-deep border-primary-line',
  ok: 'bg-ok-soft text-ok-deep border-ok-line',
  warn: 'bg-warn-soft text-warn-deep border-warn-line',
  danger: 'bg-danger-soft text-danger-deep border-danger-line',
};

export function Chip({
  children,
  tone = 'default',
  className,
}: {
  children: ReactNode;
  tone?: ChipTone;
  className?: string;
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center whitespace-nowrap rounded-[5px] border px-1.5 py-px text-[10px] leading-[15px]',
        TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}
