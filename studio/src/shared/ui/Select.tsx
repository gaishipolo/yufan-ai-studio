import { forwardRef, type SelectHTMLAttributes } from 'react';
import { ChevronDown } from 'lucide-react';
import { cn } from '@/shared/lib/cn';

export interface SelectOption {
  value: string;
  label: string;
}

export interface SelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, 'children'> {
  options: SelectOption[];
  block?: boolean;
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { options, block, className, ...rest },
  ref,
) {
  return (
    <div className={cn('relative inline-flex', block && 'w-full')}>
      <select
        ref={ref}
        className={cn(
          'h-7 w-full cursor-pointer appearance-none rounded-lg border border-line bg-white pl-2.5 pr-7 text-xs text-ink-soft',
          'transition-colors hover:border-line-strong focus:border-primary-line focus:outline-none focus:ring-2 focus:ring-primary/10',
          block && 'h-8',
          className,
        )}
        {...rest}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      <ChevronDown
        size={12}
        className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-ink-mute"
      />
    </div>
  );
});
