import { useEffect, useRef, useState } from 'react';
import { ChevronDown, ChevronRight, Plus } from 'lucide-react';
import { cn } from '@/shared/lib/cn';

export interface AddSlotOption {
  value: string;
  label: string;
  /** 有这一层就是「二级选项」：点开不再直接加槽位，而是展开这一组。 */
  children?: {value: string; label: string}[];
}

/**
 * 「加槽位」的下拉，支持一层二级选项。
 *
 * 以前是标题栏上并排五颗小按钮（成片/图片/音频/文本/封装），横着铺满半条带子，
 * 把分区标题挤到一边。收成一颗按钮后，标题栏干净了，入口也还在。
 *
 * 二级是为图片插槽预设加的：图片底下挂着一组预设，点一个就直接带出名称和
 * 选中时文案。展开用**点击**而不是 hover —— 触屏没有 hover，而且点开后能看清
 * 一整列选项，不用横着挪鼠标（那样很容易误点到旁边的类型）。
 */
export function AddSlotMenu({
  options,
  onAdd,
}: {
  options: AddSlotOption[];
  onAdd: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (!ref.current?.contains(event.target as Node)) {
        setOpen(false);
        setExpanded(null);
      }
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        if (expanded) setExpanded(null);
        else setOpen(false);
      }
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open, expanded]);

  const close = () => {
    setOpen(false);
    setExpanded(null);
  };

  const choose = (value: string) => {
    close();
    onAdd(value);
  };

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => setOpen((value) => !value)}
        className={cn(
          'inline-flex h-7 items-center gap-1 rounded-pill border border-line bg-panel px-2.5 text-xs font-medium',
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
          className="absolute right-0 top-full z-30 mt-1 w-[186px] rounded-xl border border-line bg-panel p-1 shadow-pop animate-slideDown"
        >
          {options.map((item) => (
            <div key={item.value}>
              <button
                type="button"
                role="menuitem"
                aria-expanded={item.children ? expanded === item.value : undefined}
                onClick={() => {
                  if (item.children) setExpanded((value) => (value === item.value ? null : item.value));
                  else choose(item.value);
                }}
                className="flex w-full items-center gap-1.5 rounded-lg px-2 py-1.5 text-left text-xs text-ink-soft transition-colors hover:bg-panel-soft hover:text-ink"
              >
                <Plus size={11} className="text-ink-mute" />
                <span className="min-w-0 flex-1 truncate">{item.label}</span>
                {item.children && (
                  <ChevronRight
                    size={11}
                    className={cn('text-ink-mute transition-transform', expanded === item.value && 'rotate-90')}
                  />
                )}
              </button>
              {item.children && expanded === item.value && (
                <div role="group" aria-label={`${item.label}预设`} className="ml-2 border-l border-line-soft pl-1">
                  {item.children.map((child) => (
                    <button
                      key={child.value}
                      type="button"
                      role="menuitem"
                      onClick={() => choose(child.value)}
                      className="flex w-full items-center gap-1.5 rounded-lg px-2 py-1.5 text-left text-xs text-ink-soft transition-colors hover:bg-panel-soft hover:text-ink"
                    >
                      <Plus size={10} className="text-ink-mute" />
                      <span className="min-w-0 flex-1 truncate">{child.label}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
