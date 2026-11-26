import type { ReactNode } from 'react';
import { cn } from '@/shared/lib/cn';

export type ChipTone = 'default' | 'primary' | 'brand' | 'ok' | 'warn' | 'danger';

/* 只用 token：原来这里写死了 10 个十六进制值，换主题时只有它不跟着变。
   `brand`（青）= **在飞**：只有真的在生成 / 在跑的东西才用它。 */
const TONES: Record<ChipTone, string> = {
  default: 'bg-panel-soft text-ink-soft border-line-soft',
  primary: 'bg-primary-soft text-primary-deep border-primary-line',
  brand: 'bg-brand-soft text-brand-deep border-brand-line',
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
        /* 徽章是一枚直角小标签（印刷的铅字块），不是药丸。
           字号 11px —— 徽章常和模型名、计数并排，10px 在中文里已经糊成一团。 */
        'inline-flex items-center whitespace-nowrap rounded-xs border px-1.5 py-px text-meta leading-[16px]',
        TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}
