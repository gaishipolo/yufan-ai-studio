import type { ReactNode } from 'react';
import { cn } from '@/shared/lib/cn';

export type ChipTone = 'default' | 'primary' | 'ok' | 'warn' | 'danger';

const TONES: Record<ChipTone, string> = {
  default: 'bg-[#f2f7f8] text-[#6a7d87] border-line-soft',
  primary: 'bg-primary-soft text-[#2b6bd4] border-[#dceaff]',
  ok: 'bg-[#e8faf2] text-[#0f9c63] border-[#d3f3e4]',
  warn: 'bg-[#fff6e6] text-[#b97a10] border-[#fbe8c8]',
  danger: 'bg-[#feeceb] text-[#d94036] border-[#fad7d4]',
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
