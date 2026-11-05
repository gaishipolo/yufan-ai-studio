import { forwardRef, type InputHTMLAttributes } from 'react';
import { Search } from 'lucide-react';
import { cn } from '@/shared/lib/cn';

export interface SearchInputProps extends InputHTMLAttributes<HTMLInputElement> {
  wrapperClassName?: string;
}

export const SearchInput = forwardRef<HTMLInputElement, SearchInputProps>(function SearchInput(
  { className, wrapperClassName, placeholder = '搜索…', ...rest },
  ref,
) {
  return (
    <div
      className={cn(
        'flex h-[30px] min-w-0 items-center gap-1.5 rounded-lg border border-line bg-panel-soft px-2.5',
        'transition-all focus-within:border-primary-line focus-within:bg-white focus-within:shadow-ring',
        wrapperClassName,
      )}
    >
      <Search size={13} className="shrink-0 text-ink-mute" />
      <input
        ref={ref}
        type="search"
        placeholder={placeholder}
        className={cn(
          'h-full w-full min-w-0 bg-transparent text-xs text-ink outline-none placeholder:text-[#a9b8bf]',
          '[&::-webkit-search-cancel-button]:appearance-none',
          className,
        )}
        {...rest}
      />
    </div>
  );
});
