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
          'h-8 w-full min-w-0 rounded-lg border border-line bg-white px-2.5 text-body text-ink',
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
  /**
   * 字段的**约束**，不是字段的复述。
   *
   * 「名称唯一，重名会被后端拒绝」「至少 8 位」——这些用户不看输入框猜不出来，
   * 该留。但「创建后可在小组里调整」这类在别处已经写着的话不必再写一遍。
   *
   * 它挂在**标签那一行**的右端，不再单独占一行：约束是标签的一部分，
   * 不是输入框下面的一段小字。
   */
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
      {/* 标签在左、注解推到右端：一行里只有两个东西，位置固定，
          所以「哪段是字段名、哪段是补充」永远不用猜。
          挤在标签后面时，长注解会被读成同一个词组
          （「角色标识 小写字母开头，如 content_editor」）。 */}
      <div className="flex items-baseline gap-x-2">
        <label htmlFor={htmlFor} className="shrink-0 text-meta font-medium text-ink-soft">
          {label}
          {required && (
            <span className="ml-0.5 text-danger" aria-hidden>
              *
            </span>
          )}
        </label>
        {hint && !error && (
          <span className="ml-auto min-w-0 max-w-[72%] text-right text-micro text-ink-mute">{hint}</span>
        )}
      </div>
      {children}
      {error && (
        <p className="text-meta leading-[1.5] text-danger" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
