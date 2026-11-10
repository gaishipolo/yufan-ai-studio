import type { ReactNode } from 'react';
import { cn } from '@/shared/lib/cn';

export interface SwitchProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  children?: ReactNode;
  className?: string;
  disabled?: boolean;
  /** 关联外部 label（`<label htmlFor>`），参数表单里靠它绑定字段名 */
  id?: string;
  /** 没有可见文字（或文字是「开启/关闭」这类状态词）时的可访问名称 */
  ariaLabel?: string;
}

export function Switch({
  checked,
  onChange,
  children,
  className,
  disabled,
  id,
  ariaLabel,
}: SwitchProps) {
  return (
    <label
      className={cn(
        'inline-flex cursor-pointer select-none items-center gap-1.5 text-xs text-ink-soft',
        disabled && 'cursor-not-allowed opacity-50',
        className,
      )}
    >
      <input
        id={id}
        type="checkbox"
        className="peer sr-only"
        checked={checked}
        disabled={disabled}
        aria-label={ariaLabel}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span
        className={cn(
          'relative h-[15px] w-[26px] rounded-full bg-[#d9e4e8] transition-colors duration-150',
          'peer-checked:bg-primary',
          'after:absolute after:left-[2px] after:top-[2px] after:h-[11px] after:w-[11px] after:rounded-full',
          'after:bg-white after:shadow-[0_1px_2px_rgba(0,0,0,.2)] after:transition-transform after:duration-150',
          'peer-checked:after:translate-x-[11px]',
          'peer-focus-visible:ring-2 peer-focus-visible:ring-primary/25',
        )}
      />
      {children}
    </label>
  );
}
