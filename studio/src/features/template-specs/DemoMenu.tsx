import { useEffect, useRef, useState } from 'react';
import { ChevronDown, ImagePlus, Upload, Video } from 'lucide-react';
import { cn } from '@/shared/lib/cn';

/**
 * 「设置案例」的下拉：上传 / 选图片 / 选视频三选一。
 *
 * 以前是标题栏上并排三颗按钮，占掉整条标题带子的宽度，而其中「选视频」十次里
 * 不到一次用（案例以图片为主）。收进一个菜单后，标题栏只留一颗按钮，
 * 三个入口还在。
 */
export function DemoMenu({
  disabled,
  onUpload,
  onPickImage,
  onPickVideo,
}: {
  disabled?: boolean;
  onUpload: () => void;
  onPickImage: () => void;
  onPickVideo: () => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // 点外面就收起：菜单是临时的，不该挡住别处又不消失。
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

  const items = [
    {label: '上传文件', icon: <Upload size={12} />, run: onUpload},
    {label: '从资产库选图片', icon: <ImagePlus size={12} />, run: onPickImage},
    {label: '从资产库选视频', icon: <Video size={12} />, run: onPickVideo},
  ];

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        disabled={disabled}
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => setOpen(value => !value)}
        className={cn(
          'inline-flex h-7 items-center gap-1 rounded-lg border border-transparent px-2.5 text-xs font-medium',
          'text-ink-soft transition-colors hover:bg-[#f1f6f7] hover:text-ink',
          'disabled:cursor-not-allowed disabled:opacity-50',
          open && 'bg-[#f1f6f7] text-ink',
        )}
      >
        设置案例
        <ChevronDown size={12} className={cn('transition-transform', open && 'rotate-180')} />
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 top-full z-30 mt-1 w-[168px] rounded-xl border border-line bg-panel p-1 shadow-pop animate-slideDown"
        >
          {items.map(item => (
            <button
              key={item.label}
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false);
                item.run();
              }}
              className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[12px] text-ink-soft transition-colors hover:bg-panel-soft hover:text-ink"
            >
              {item.icon}
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
