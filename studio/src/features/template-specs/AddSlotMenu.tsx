import { useEffect, useRef, useState } from 'react';
import { ChevronDown, Plus } from 'lucide-react';
import { cn } from '@/shared/lib/cn';

/**
 * 「加槽位」的下拉。
 *
 * 以前是标题栏上并排五颗小按钮（成片/图片/音频/文本/封装），横着铺满半条带子，
 * 把分区标题挤到一边。收成一颗按钮后，标题栏干净了，入口也还在。
 *
 * 选项仍按资产库的 kind 生成 —— 库里多一类，这里就多一档。
 */
export function AddSlotMenu({
  options,
  onAdd,
}: {
  options: {value: string; label: string}[];
  onAdd: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (!ref.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => setOpen(value => !value)}
        className={cn(
          'inline-flex h-7 items-center gap-1 rounded-lg border border-line bg-panel px-2.5 text-xs font-medium',
          'text-ink-soft transition-colors hover:border-line-strong hover:text-ink',
          open && 'border-primary-line bg-primary-soft text-primary',
        )}
      >
        <Plus size={12} />
        加槽位
        <ChevronDown size={12} className={cn('transition-transform', open && 'rotate-180')} />
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 top-full z-30 mt-1 w-[132px] rounded-xl border border-line bg-panel p-1 shadow-pop animate-slideDown"
        >
          {options.map(item => (
            <button
              key={item.value}
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                onAdd(item.value);
              }}
              className="flex w-full items-center gap-1.5 rounded-lg px-2 py-1.5 text-left text-xs text-ink-soft transition-colors hover:bg-panel-soft hover:text-ink"
            >
              <Plus size={11} className="text-ink-mute" />
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
