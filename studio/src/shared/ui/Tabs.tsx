import { cn } from '@/shared/lib/cn';

export interface TabItem<T extends string = string> {
  value: T;
  label: string;
  count?: number;
}

export interface TabsProps<T extends string = string> {
  items: TabItem<T>[];
  value: T;
  onChange: (value: T) => void;
  className?: string;
}

export function Tabs<T extends string = string>({ items, value, onChange, className }: TabsProps<T>) {
  return (
    <div className={cn('flex flex-wrap items-center gap-1.5', className)}>
      {items.map((item) => {
        const active = item.value === value;
        return (
          <button
            key={item.value}
            type="button"
            onClick={() => onChange(item.value)}
            className={cn(
              'inline-flex h-[27px] items-center gap-1.5 rounded-pill px-2.5 text-xs transition-all duration-150',
              active
                ? 'bg-primary font-semibold text-white shadow-[0_3px_9px_rgba(43,124,255,.28)]'
                : 'text-ink-soft hover:bg-[#f2f7f8]',
            )}
          >
            {item.label}
            {typeof item.count === 'number' && (
              <span
                className={cn(
                  'text-[11.5px] tabular-nums',
                  active ? 'text-white/85' : 'text-ink-mute',
                )}
              >
                {item.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
