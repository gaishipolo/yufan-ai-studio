import { forwardRef, type InputHTMLAttributes, type ReactNode } from 'react';
import { cn } from '@/shared/lib/cn';

/**
 * 表单输入基元。
 * 只做「外观 + 可访问性」，不带校验逻辑 —— 校验规则归各自的领域（后端返回什么就显示什么）。
 */
export const TextInput = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(
  function TextInput({ className, ...rest }, ref) {
    return (
      <input
        ref={ref}
        className={cn(
          'h-8 w-full min-w-0 rounded-lg border border-line bg-white px-2.5 text-xs text-ink',
          'outline-none transition-colors placeholder:text-[#a9b8bf]',
          'hover:border-line-strong focus:border-primary-line focus:ring-2 focus:ring-primary/10',
          'disabled:cursor-not-allowed disabled:bg-panel-soft disabled:text-ink-mute',
          'aria-[invalid=true]:border-danger',
          className,
        )}
        {...rest}
      />
    );
  },
);

export interface FormFieldProps {
  label: string;
  /** 关联到输入控件的 id，label 点击可聚焦 */
  htmlFor?: string;
  hint?: string;
  /** 有错时优先展示错误，不再展示 hint（避免两行小字打架） */
  error?: string;
  required?: boolean;
  children: ReactNode;
  className?: string;
}

export function FormField({
  label,
  htmlFor,
  hint,
  error,
  required,
  children,
  className,
}: FormFieldProps) {
  return (
    <div className={cn('flex flex-col gap-1', className)}>
      <label htmlFor={htmlFor} className="text-[11.5px] font-medium text-ink-soft">
        {label}
        {required && (
          <span className="ml-0.5 text-danger" aria-hidden>
            *
          </span>
        )}
      </label>
      {children}
      {error ? (
        <p className="text-[11px] leading-[1.5] text-danger" role="alert">
          {error}
        </p>
      ) : hint ? (
        <p className="text-[11px] leading-[1.5] text-ink-mute">{hint}</p>
      ) : null}
    </div>
  );
}
