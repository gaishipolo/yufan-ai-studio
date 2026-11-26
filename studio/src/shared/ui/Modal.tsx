import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { cn } from '@/shared/lib/cn';

/* 弹窗栈：多层弹窗叠加时，只有**最上面**那个能响应 Escape，也只有它决定 z-index。
   以前这里只有个引用计数，Escape 是每个弹窗各自监听 window —— 两层叠着时按一次
   Escape 会把两层一起关掉，用户填了一半的表单就没了。
   叠层本身不是新需求：「建号弹窗里开「新建角色」」这种场景已经存在，
   只是以前没人把入口摆在弹窗里。 */
const modalStack: symbol[] = [];
let savedOverflow = '';

/** 栈顶弹窗的 z-index 基准；每层往上加 10，保证叠放顺序与 DOM 顺序无关 */
const Z_BASE = 100;

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
  /* 栈里的身份：同一个弹窗的多次开关要认得出是「同一个」，否则栈会脏。 */
  const selfId = useRef(Symbol('modal'));
  /* 栈位要进 state：z-index 在 render 期就得算出来，不能等 effect 跑完。 */
  const [stackIndex, setStackIndex] = useState(0);

  /* 用 layout effect 而不是 useEffect：栈位必须在**绘制前**算好，
     否则叠层的第一帧会用底层那个的 z-index 画出来，闪一下。 */
  useLayoutEffect(() => {
    if (!open) return undefined;

    /* 局部副本：cleanup 里要用的是「入栈时那个」的身份，
       直接读 `selfId.current` 会被 lint 判成「ref 可能已变」。 */
    const id = selfId.current;
    if (modalStack.length === 0) savedOverflow = document.body.style.overflow;
    modalStack.push(id);
    setStackIndex(modalStack.length - 1);
    document.body.style.overflow = 'hidden';

    return () => {
      const at = modalStack.indexOf(id);
      if (at >= 0) modalStack.splice(at, 1);
      if (modalStack.length === 0) document.body.style.overflow = savedOverflow;
    };
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;

    const id = selfId.current;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      /* 只有栈顶响应：下面那层是「背景」，用户按 Esc 想关的是眼前这层 */
      if (modalStack[modalStack.length - 1] !== id) return;
      closeRef.current();
    };
    window.addEventListener('keydown', onKeyDown);

    /* 焦点进入弹窗、关闭后归还触发元素（CODE_STYLE 3.7） */
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const focusTimer = window.setTimeout(() => dialogRef.current?.focus(), 0);

    return () => {
      window.removeEventListener('keydown', onKeyDown);
      window.clearTimeout(focusTimer);
      previouslyFocused?.focus?.();
    };
  }, [open]);

  if (!open) return null;

  /* z-index 按栈位算：叠层时后开的那个一定在上面，跟它在 DOM 里的位置无关。 */
  const zIndex = Z_BASE + stackIndex * 10;

  return createPortal(
    <div
      role="presentation"
      style={{ zIndex }}
      className="fixed inset-0 grid place-items-center overflow-auto bg-[rgba(12,24,30,.55)] p-6 backdrop-blur-[3px] animate-fadeIn"
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
          /* 弹窗是「压在台面上的东西」：半径给到 16（面板级），
             配 shadow-pop。它跟白卡的 12 拉开一档，
             用户不用看描边就知道这层浮在别的东西上面。 */
          'max-h-[88vh] max-w-[94vw] overflow-auto rounded-2xl bg-panel shadow-pop animate-popIn',
          'outline-none',
          className,
        )}
      >
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-line-soft bg-panel px-[18px] py-[15px]">
          <div id={titleId} className="font-serif text-lead font-semibold text-ink">
            {title}
          </div>
          <button
            type="button"
            aria-label="关闭"
            onClick={onClose}
            className="grid h-[26px] w-[26px] place-items-center rounded-lg text-ink-mute transition-colors hover:bg-panel-soft hover:text-ink"
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
