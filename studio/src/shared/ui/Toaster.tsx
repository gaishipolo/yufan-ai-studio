import { cn } from '@/shared/lib/cn';
import { useToastStore } from '@/shared/lib/toast';

export function Toaster() {
  const items = useToastStore((s) => s.items);

  if (!items.length) return null;

  return (
    <div className="fixed left-1/2 top-3.5 z-[200] flex -translate-x-1/2 flex-col gap-2">
      {items.map((item) => (
        <div
          key={item.id}
          className={cn(
            'animate-popIn rounded-[9px] px-3.5 py-2 text-xs shadow-pop',
            item.tone === 'error' ? 'bg-danger text-white' : 'bg-[#1f2f38] text-white',
          )}
        >
          {item.message}
        </div>
      ))}
    </div>
  );
}
