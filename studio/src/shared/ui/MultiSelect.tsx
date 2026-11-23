import {
  cloneElement,
  isValidElement,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from 'react';
import { createPortal } from 'react-dom';
import { Check, ChevronDown } from 'lucide-react';
import { cn } from '@/shared/lib/cn';
import { useDropPlacement } from '@/shared/hooks/useDropPlacement';

/** 浮层大致高度：列表 196 + 内边距 + 新建行。下方不够就朝上展开。 */
const POPOVER_MIN_SPACE = 250;

/** 浮层渲染到 body 时的层级：必须高于 Modal 的 Z_BASE(100) + 栈位偏移，
    否则在弹窗里展开的浮层会被弹窗自己盖住。 */
const POPOVER_Z_INDEX = 300;

export interface MultiSelectOption {
  value: string;
  label: string;
  /** 第二行说明：角色显示标识，分组显示小组说明 */
  description?: string;
  /** 右上角小标记，如「超管」「内置」 */
  badge?: string;
}

export interface MultiSelectProps {
  options: MultiSelectOption[];
  value: string[];
  onChange: (values: string[]) => void;
  /** 未选择时收起态的文案 */
  placeholder?: string;
  /** 一个选项都没有时的空态文案 */
  emptyText?: string;
  /**
   * 空态里的行动入口。
   *
   * 空态文案本身是个**死角**：告诉用户「还没有角色，请先去另一个页签创建」，
   * 却不给路。`emptyAction` 把那个入口直接摆出来 —— 死胡同里必须留个门。
   *
   * 点完这个入口，浮层会**自动收起**（见 `closeOnEmptyAction` 的处理）：
   * 浮层开着的时候它会抢走 Escape 键（用户以为关的是刚开的弹窗，
   * 结果只是收起了下拉），而且它盖在新建弹窗下面，看着像没反应。
   * 收起动作注入在 `onClick` 里 —— 不能提前到 `pointerdown`，
   * 那一刻按钮还没收到 click，浮层一关按钮就被卸载，click 永远不触发。
   */
  emptyAction?: ReactNode;
  disabled?: boolean;
  /** 关联 FormField 的 label（label 点击会聚焦这个按钮） */
  id?: string;
  /** 可访问名；不传则用收起态文本 */
  ariaLabel?: string;
}

/**
 * 多选下拉：收起时是一行，展开后是带复选框的选项列表。
 *
 * 为什么不直接用原生 `<select multiple>`：它在浏览器里渲染成固定高度列表框，
 * 既不是「下拉」，也没法给每项带上第二行说明。而建号弹窗里角色需要多选，
 * 又要把原来占半屏的复选框长列表收起来。
 *
 * **浮层方向按空间自适应**：默认向下展开；下方放大约 250px 时向上翻。
 * 原先写死向下，在「贴底部的批量栏」里展开后整块落在视口外，用户只看到一条
 * 被裁掉的边——控件明明在那里，就是点不到。
 *
 * 组件只管「选哪些」，不认识角色或小组；选项由调用方从服务端数据映射。
 */
export function MultiSelect({
  options,
  value,
  onChange,
  placeholder = '请选择',
  emptyText = '暂无可选项',
  emptyAction,
  disabled = false,
  id,
  ariaLabel,
}: MultiSelectProps) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const listId = useId();
  /* 浮层朝上还是朝下：量出来的，不是声明的。贴底的批量栏里朝下展开会落到视口外
     （只看得到一条边）。方向逻辑在 `useDropPlacement` 里，模板封装的 @ 候选面板
     用的是同一份 —— 两份实现漂移就是「同一个浮层两个页面表现不一样」。 */
  const { side, space } = useDropPlacement(rootRef, listRef, open, POPOVER_MIN_SPACE);
  const dropUp = side === 'top';

  /* 浮层用 Portal 渲染到 body（见下方 JSX），所以不能靠 `absolute` 挂在 root 上 ——
     Modal 内容区是 `overflow-auto`，祖先的裁剪会把浮层切掉（资产库上传弹窗里
     下拉框只露出半截）。这里自己记住锚点的视口坐标，用 `fixed` 定位绕开所有祖先。
     useDropPlacement 只回答方向/剩余空间，不含坐标；它还要给模板 @ 候选面板用，
     不为一个调用点改返回形状。 */
  const [anchorRect, setAnchorRect] = useState<DOMRect | null>(null);
  useLayoutEffect(() => {
    if (!open) {
      setAnchorRect(null);
      return undefined;
    }
    const measure = () => {
      const rect = rootRef.current?.getBoundingClientRect();
      if (rect) setAnchorRect(rect);
    };
    measure();
    window.addEventListener('scroll', measure, true);
    window.addEventListener('resize', measure);
    return () => {
      window.removeEventListener('scroll', measure, true);
      window.removeEventListener('resize', measure);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      /* Portal 出去的浮层不在 rootRef 里 —— 不把 listRef 也算进来，
         点 checkbox 会被当成「点在外面」，浮层当场收起，一个都选不上。 */
      if (!rootRef.current?.contains(target) && !listRef.current?.contains(target)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        setOpen(false);
      }
    };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeyDown, true);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown, true);
    };
  }, [open]);

  const labelOf = (item: string) =>
    options.find((option) => option.value === item)?.label ?? item;
  const selectedLabels = value.map(labelOf);
  // 收起态只占一行：多选时不把所有名字铺出来（否则弹窗会被撑变形），
  // 完整清单放 title，hover 即可看到。
  const summary =
    value.length === 0
      ? placeholder
      : value.length === 1
        ? selectedLabels[0]
        : `已选 ${value.length} 项`;

  const toggle = (item: string) => {
    onChange(value.includes(item) ? value.filter((v) => v !== item) : [...value, item]);
  };

  /* 空态动作点完要收起浮层。收起逻辑注入到子元素的 `onClick` 里：
     不能靠 `pointerdown`（那时 click 还没发生，按钮会随浮层一起被卸载），
     也不能靠外层 div 的冒泡（a11y 规则不允许 div 带 click 处理器）。
     调用方给的 `onClick` 保留 —— 收起是**追加**行为，不是替换。 */
  const emptyActionNode = useMemo(() => {
    if (!emptyAction || !isValidElement(emptyAction)) return emptyAction;
    const element = emptyAction as ReactElement<{ onClick?: (event: React.MouseEvent) => void }>;
    const original = element.props.onClick;
    return cloneElement(element, {
      onClick: (event: React.MouseEvent) => {
        original?.(event);
        setOpen(false);
      },
    });
  }, [emptyAction]);

  /* 打开时量一次可用空间再决定方向，而不是靠调用方传 placement：
     同一个组件在批量栏（贴底）和详情弹窗（页面中部）要用不同方向，量比重申可靠。
     估算高度 = 列表 196 + 内边距 + 新建行，取 250。 */
  const toggleOpen = () => {
    setOpen(!open);
  };

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        id={id}
        aria-label={ariaLabel}
        aria-expanded={open}
        aria-controls={listId}
        disabled={disabled}
        title={selectedLabels.join('、') || undefined}
        onClick={toggleOpen}
        className={cn(
          'flex h-8 w-full items-center gap-1.5 rounded-lg border border-line bg-white px-2.5 text-left text-xs',
          'transition-colors hover:border-line-strong focus:border-primary-line focus:outline-none focus:ring-2 focus:ring-primary/10',
          'disabled:cursor-not-allowed disabled:bg-panel-soft',
          open && 'border-primary-line',
        )}
      >
        <span className={cn('min-w-0 flex-1 truncate', value.length === 0 && 'text-ink-mute')}>
          {summary}
        </span>
        <ChevronDown
          size={12}
          className={cn('shrink-0 text-ink-mute transition-transform', open && 'rotate-180')}
        />
      </button>

      {open && anchorRect && createPortal(
        <div
          id={listId}
          ref={listRef}
          data-placement={dropUp ? 'top' : 'bottom'}
          /* fixed 定位：脱离 Modal 的 overflow-auto 祖先，浮层不再被弹窗边框切掉。
             maxHeight 用 hook 量出的那一侧剩余空间封顶，别让它溢出视口。 */
          style={{
            position: 'fixed',
            left: anchorRect.left,
            width: anchorRect.width,
            zIndex: POPOVER_Z_INDEX,
            maxHeight: Math.max(96, space),
            ...(dropUp
              ? { bottom: window.innerHeight - anchorRect.top + 4 }
              : { top: anchorRect.bottom + 4 }),
          } satisfies CSSProperties}
          className="overflow-hidden rounded-lg border border-line bg-white shadow-pop"
        >
          {options.length === 0 ? (
            <div className="px-3 py-4 text-center">
              <p className="text-meta text-ink-mute">{emptyText}</p>
              {emptyActionNode && (
                <div className="mt-2 flex justify-center">{emptyActionNode}</div>
              )}
            </div>
          ) : (
            <ul className="max-h-[196px] space-y-0.5 overflow-auto p-1.5">
              {options.map((option) => {
                const checked = value.includes(option.value);
                return (
                  <li key={option.value}>
                    <label
                      className={cn(
                        'flex cursor-pointer items-center gap-2 rounded-[7px] px-2 py-1.5 transition-colors',
                        checked ? 'bg-primary-soft' : 'hover:bg-panel-soft',
                      )}
                    >
                      <input
                        type="checkbox"
                        className="h-3 w-3 shrink-0 accent-primary"
                        checked={checked}
                        disabled={disabled}
                        onChange={() => toggle(option.value)}
                      />
                      <span className="flex min-w-0 flex-1 flex-col">
                        <span className="flex min-w-0 items-center gap-1.5">
                          <span className="truncate text-xs text-ink">{option.label}</span>
                          {option.badge && (
                            <span className="shrink-0 rounded bg-primary-soft px-1 py-px text-micro text-primary">
                              {option.badge}
                            </span>
                          )}
                        </span>
                        {option.description && (
                          <span className="truncate text-meta text-ink-mute">
                            {option.description}
                          </span>
                        )}
                      </span>
                      {checked && <Check size={12} className="shrink-0 text-primary" />}
                    </label>
                  </li>
                );
              })}
            </ul>
          )}
        </div>,
        document.body,
      )}
    </div>
  );
}
