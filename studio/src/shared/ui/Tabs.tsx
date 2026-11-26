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
              /* 报纸栏目式页签：没有药丸、没有底色块，
                 只有一条下划线 —— 选中是墨线墨字，未选中是浅字。
                 页签是「目录里的条目」，不是按钮。 */
              'inline-flex h-[27px] items-center gap-1.5 border-b-2 px-3 text-xs transition-colors duration-150',
              active
                ? 'border-primary font-semibold text-ink'
                : 'border-transparent text-ink-mute hover:text-ink-soft',
            )}
          >
            {item.label}
            {typeof item.count === 'number' && (
              <span
                className={cn(
                  'text-meta tabular-nums',
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
