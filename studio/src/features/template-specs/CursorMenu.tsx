import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { cn } from '@/shared/lib/cn';

/**
 * 浮在**光标处**的窄菜单（提示词编辑区的 @ 候选 / 框选指派共用）。
 *
 * ## 为什么不是「挂在输入框下方的等宽选择框」
 *
 * 提示词框是宽的一条，候选却只有四五项。等宽铺满时，真正要读的那一列字
 * 被拉到左边一窄条，右边大片空白；更糟的是它挡住下面的随机档输入框。
 * 参考系统右键菜单的做法：**贴着光标弹出、宽度由内容定、分组用发丝线**。
 * 眼睛不用从光标跳到别处 —— 菜单就在打字的地方。
 *
 * ## 定位
 *
 * 锚点是**视口坐标**（光标矩形），不是 DOM 元素：光标会随输入移动，
 * 而菜单一旦弹出就不该跟着每个字符乱跳（打开那一刻定住）。
 * 用 `position: fixed` + portal，脱离编辑器的 overflow 祖先，不被裁。
 * 靠近视口右下角时翻到左上 —— 写死朝右下会在页面底部掉出去。
 */
export interface CaretPoint {
  x: number;
  y: number;
}

const GAP = 6;
const EDGE = 8;

export function CursorMenu({
  point,
  onDismiss,
  width = 236,
  maxHeight = 300,
  children,
}: {
  point: CaretPoint | null;
  onDismiss: () => void;
  width?: number;
  maxHeight?: number;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({left: 0, top: 0, ready: false, flipX: false, flipY: false});

  useLayoutEffect(() => {
    if (!point || !ref.current) return;
    const el = ref.current;
    const w = el.offsetWidth;
    const h = el.offsetHeight;
    /* 优先贴在光标右下；右边/下边放不下就翻到左/上。
       用 offsetWidth/Height 实测，不猜候选条数。 */
    let left = point.x + GAP;
    let top = point.y + GAP;
    const flipX = left + w + EDGE > window.innerWidth;
    const flipY = top + h + EDGE > window.innerHeight;
    if (flipX) left = Math.max(EDGE, point.x - w - GAP);
    if (flipY) top = Math.max(EDGE, point.y - h - GAP);
    setPos({left, top, ready: true, flipX, flipY});
  }, [point, children]);

  useEffect(() => {
    if (!point) return;
    const onDown = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) onDismiss();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onDismiss();
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [point, onDismiss]);

  if (!point) return null;

  return createPortal(
    <div
      ref={ref}
      role="menu"
      tabIndex={-1}
      data-placement={pos.flipY ? 'top' : 'bottom'}
      data-flip-x={pos.flipX || undefined}
      style={{
        position: 'fixed',
        left: pos.left,
        top: pos.top,
        width,
        maxHeight,
        visibility: pos.ready ? 'visible' : 'hidden',
      }}
      /* mousedown 不冒泡到外面：候选按钮靠 preventDefault 保住编辑器选区，
         但空白处点下去也别把菜单关了（用户可能只是想挪一下鼠标）。 */
      onMouseDown={(event) => event.stopPropagation()}
      className={cn(
        'z-50 overflow-hidden rounded-lg border border-line bg-panel shadow-pop',
        'animate-popIn',
      )}
    >
      <div className="max-h-[inherit] overflow-y-auto scroll-thin p-1">
        {children}
      </div>
    </div>,
    document.body,
  );
}

/** 菜单里的一行：左边标签、右边次要提示（token / 类型 / 快捷键）。
    与参考图同一套排版：两栏对齐，读的人一眼扫完。 */
export function CursorMenuItem({
  label,
  hint,
  active,
  icon,
  onSelect,
  onHover,
}: {
  label: string;
  hint?: string;
  active?: boolean;
  icon?: ReactNode;
  /** 用 mousedown 选中：编辑器会在 blur 时清掉选区/提及状态。 */
  onSelect: () => void;
  onHover?: () => void;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      onMouseDown={(event) => {
        event.preventDefault();
        onSelect();
      }}
      onMouseEnter={onHover}
      className={cn(
        'flex w-full items-center gap-2 rounded-xs px-2 py-1.5 text-left text-xs transition-colors',
        active ? 'bg-primary-soft text-primary-deep' : 'text-ink-soft hover:bg-panel-soft',
      )}
    >
      {icon && <span className="flex shrink-0 items-center text-ink-mute">{icon}</span>}
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {hint && <span className="shrink-0 text-micro text-ink-mute">{hint}</span>}
    </button>
  );
}

/** 分组标题 + 发丝线：参考图里「表情符号」下面那道分隔。 */
export function CursorMenuGroup({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="border-b border-line-soft pb-1 first:border-0 first:pb-0">
      <p className="px-2 py-1 text-micro text-ink-mute">{label}</p>
      {children}
    </div>
  );
}
