import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { Loader2 } from 'lucide-react';
import { cn } from '@/shared/lib/cn';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';
export type ButtonSize = 'sm' | 'md' | 'lg';

/* 按钮是 pill —— 不是风格偏好，是**形状语言**：方角按钮和圆角按钮放在
   同一屏里，用户要重新读一遍「这个能不能点」；全站统一成 pill，
   「能点的」和「只是内容的」在形状上就分开了（视觉规范 §3.5.1：
   方角按钮在这个语言里等于「第三方控件」）。
   主按钮是**实心 + 无投影**：浮起来的蓝底会跟蓝色焦点环打架，
   也让一屏里几个主按钮互相抢眼；实心色块本身已经足够「按我」。 */
const VARIANTS: Record<ButtonVariant, string> = {
  /* 墨块，不是蓝块。纸感编辑部里没有「蓝色按钮」这种东西 ——
     主动作就是一块墨，按下去是更深的墨。 */
  primary:
    'bg-cta text-white border border-cta hover:bg-cta-deep active:bg-cta-deep',
  secondary:
    'bg-panel text-ink-soft border border-line-strong hover:border-ink hover:text-ink hover:bg-panel-soft',
  ghost: 'bg-transparent text-ink-soft border border-transparent hover:bg-panel-soft hover:text-ink',
  danger: 'bg-danger text-white border border-danger hover:brightness-95',
};

const SIZES: Record<ButtonSize, string> = {
  sm: 'h-7 px-2.5 text-xs gap-1.5',
  md: 'h-8 px-3 text-xs gap-1.5',
  lg: 'h-9 px-3.5 text-body gap-2',
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: ReactNode;
  loading?: boolean;
  block?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'secondary', size = 'md', icon, loading, block, className, children, disabled, ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={rest.type ?? 'button'}
      disabled={disabled || loading}
      className={cn(
        /* 直角、微字距、只换颜色不换形状 —— 纸上的按钮不该有药丸。 */
        'inline-flex items-center justify-center whitespace-nowrap rounded-md font-semibold tracking-[.02em] transition-colors duration-150',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40',
        'focus-visible:ring-offset-1 focus-visible:ring-offset-panel',
        'disabled:cursor-not-allowed disabled:opacity-50',
        VARIANTS[variant],
        SIZES[size],
        block && 'w-full',
        className,
      )}
      {...rest}
    >
      {loading ? <Loader2 size={13} className="animate-spin" /> : icon}
      {children}
    </button>
  );
});
