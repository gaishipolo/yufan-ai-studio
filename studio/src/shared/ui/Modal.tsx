import { useEffect, useId, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { cn } from '@/shared/lib/cn';

/* 引用计数：多层弹窗叠加时，只有最后一个关闭才恢复滚动。
   另外 onClose 用 ref 持有——父组件传内联箭头函数时引用每次渲染都变，
   如果把 onClose 放进 deps，effect 会反复 cleanup + 重跑，
   从而把「已经被自己锁住的 overflow:hidden」当成原值记下来，最后永久锁死滚动。 */
let openModals = 0;
let savedOverflow = '';

export interface ModalProps {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  children: ReactNode;
  width?: number;
  className?: string;
}

export function Modal({ open, onClose, title, children, width = 780, className }: ModalProps) {
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  const dialogRef = useRef<HTMLDivElement>(null);
  const titleId = useId();

  useEffect(() => {
    if (!open) return undefined;

    if (openModals === 0) savedOverflow = document.body.style.overflow;
    openModals += 1;
    document.body.style.overflow = 'hidden';

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closeRef.current();
    };
    window.addEventListener('keydown', onKeyDown);

    /* 焦点进入弹窗、关闭后归还触发元素（CODE_STYLE 3.7） */
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const focusTimer = window.setTimeout(() => dialogRef.current?.focus(), 0);

    return () => {
      window.removeEventListener('keydown', onKeyDown);
      window.clearTimeout(focusTimer);
      openModals = Math.max(0, openModals - 1);
      if (openModals === 0) document.body.style.overflow = savedOverflow;
      previouslyFocused?.focus?.();
    };
  }, [open]);

  if (!open) return null;

  return createPortal(
    <div
      role="presentation"
      className="fixed inset-0 z-[100] grid place-items-center overflow-auto bg-[rgba(12,24,30,.55)] p-6 backdrop-blur-[3px] animate-fadeIn"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) closeRef.current();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? titleId : undefined}
        tabIndex={-1}
        style={{ width }}
        className={cn(
          'max-h-[88vh] max-w-[94vw] overflow-auto rounded-[14px] bg-panel shadow-pop animate-popIn',
          'outline-none',
          className,
        )}
      >
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-line-soft bg-panel px-[18px] py-[15px]">
          <div id={titleId} className="text-sm font-semibold text-ink">
            {title}
          </div>
          <button
            type="button"
            aria-label="关闭"
            onClick={onClose}
            className="grid h-[26px] w-[26px] place-items-center rounded-lg text-ink-mute transition-colors hover:bg-[#eef4f6] hover:text-ink"
          >
            <X size={15} />
          </button>
        </div>
        {children}
      </div>
    </div>,
    document.body,
  );
}
