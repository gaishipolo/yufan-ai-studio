import {useEffect, useId, useRef, useState} from 'react';
import {Check, ChevronDown, GripVertical} from 'lucide-react';
import {cn} from '@/shared/lib/cn';
import {useDropPlacement} from '@/shared/hooks/useDropPlacement';
import {MAX_ALLOWED_MODELS} from './specDraft';
import type {ModelSpec} from '@/entities/model/model/types';

/** 浮层大致高度：列表 + 内边距。下方不够就朝上展开。 */
const POPOVER_MIN_SPACE = 250;

export interface ModelPickerProps {
  /** 模型目录里的全部模型（视频 + 生图） */
  models: ModelSpec[];
  /** 已勾选的模型 id，**顺序即默认序**：第一个是默认模型 */
  value: string[];
  onChange: (values: string[]) => void;
}

/**
 * 模型选择器：一个多选控件，**勾选顺序就是默认顺序**。
 *
 * 为什么不用 `MultiSelect`：`MultiSelect` 的选中集是**集合**（勾选顺序不保留），
 * 而这里「第一个勾的是默认模型」是硬要求 —— 作者排的序 = 使用者看到的默认序。
 * 拿 `MultiSelect` 顶上就会退回「两个控件」（单选默认 + 多选清单），那正是
 * 要消掉的东西。
 *
 * 交互：**点选项 = 勾上/取消**，已勾的按当前顺序排在列表最前；拖动手柄可以
 * 改顺序（改的就是默认序）。第一个已勾的标「默认」。
 *
 * 顺序存在哪：`value` 数组本身。取消勾选时其余项**保持相对顺序**（不重排），
 * 免得用户取消一个中间项、默认模型就莫名其妙换了。
 */
export function ModelPicker({models, value, onChange}: ModelPickerProps) {
  const [open, setOpen] = useState(false);
  /* 勾到上限了：给一句明说，别让用户提交后才撞 422。 */
  const [full, setFull] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const listId = useId();
  const {side} = useDropPlacement(rootRef, listRef, open, POPOVER_MIN_SPACE);
  const dropUp = side === 'top';

  useEffect(() => {
    if (!open) return undefined;
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
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

  const labelOf = (id: string) => models.find(item => item.id === id)?.label ?? id;

  /* 已勾的排前面（按当前顺序），未勾的按目录顺序接在后面。
     用户展开列表时，「我选了什么、默认是谁」一眼看得见。 */
  const selected = value.filter(id => models.some(item => item.id === id));
  const rest = models.filter(item => !selected.includes(item.id));
  const rows = [
    ...selected.map(id => ({id, label: labelOf(id), picked: true})),
    ...rest.map(item => ({id: item.id, label: item.label, picked: false})),
  ];

  const toggle = (id: string) => {
    if (value.includes(id)) {
      onChange(value.filter(item => item !== id));
      return;
    }
    /* 超过上限就不让勾：后端会 422，而界面上看不出问题 ——
       与其让用户提交后才撞上，不如在这里就停住并说清为什么。 */
    if (value.length >= MAX_ALLOWED_MODELS) {
      setFull(true);
      return;
    }
    setFull(false);
    // 新勾的排**末尾**：默认模型是「第一个」，不是「最后勾的」。
    // 末尾追加 = 勾选顺序即默认序，一眼能对上。
    onChange([...value, id]);
  };

  /* 拖动改顺序：把 from 挪到 to 的位置。 */
  const move = (from: number, to: number) => {
    if (from === to) return;
    const next = [...value];
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    onChange(next);
  };

  /* 拖拽排序用 HTML5 DnD：拖的是**已勾选项**（未勾的没有顺序可言）。
     `dragId` 记着正在拖哪一项 —— 不能用 dataTransfer 读，jsdom 里它读不出
     真实值，而「拖的是谁」是排序正确性的关键。 */
  const [dragId, setDragId] = useState<string | null>(null);
  const [overId, setOverId] = useState<string | null>(null);

  const onDragStart = (id: string) => (event: React.DragEvent) => {
    setDragId(id);
    event.dataTransfer.effectAllowed = 'move';
    // Firefox 要求 setData 才会真的开始拖
    event.dataTransfer.setData('text/plain', id);
  };

  const onDragOver = (id: string) => (event: React.DragEvent) => {
    if (!dragId || dragId === id) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    setOverId(id);
  };

  const onDrop = (id: string) => (event: React.DragEvent) => {
    event.preventDefault();
    setOverId(null);
    if (!dragId || dragId === id) {
      setDragId(null);
      return;
    }
    move(value.indexOf(dragId), value.indexOf(id));
    setDragId(null);
  };

  const onDragEnd = () => {
    setDragId(null);
    setOverId(null);
  };

  const summary =
    value.length === 0
      ? '选择模型'
      : value.length === 1
        ? labelOf(value[0])
        : `${labelOf(value[0])} 等 ${value.length} 款`;

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        aria-label="模型"
        aria-expanded={open}
        aria-controls={listId}
        title={value.map(labelOf).join('、') || undefined}
        onClick={() => setOpen(!open)}
        className={cn(
          'flex h-8 w-full items-center gap-1.5 rounded-lg border border-line bg-white px-2.5 text-left text-xs',
          'transition-colors hover:border-line-strong focus:border-primary-line focus:outline-none focus:ring-2 focus:ring-primary/10',
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

      {open && (
        <div
          id={listId}
          ref={listRef}
          data-placement={dropUp ? 'top' : 'bottom'}
          className={cn(
            'absolute left-0 right-0 z-30 overflow-hidden rounded-lg border border-line bg-white shadow-pop',
            dropUp ? 'bottom-[calc(100%+4px)]' : 'top-[calc(100%+4px)]',
          )}
        >
          {models.length === 0 ? (
            <p className="px-3 py-4 text-center text-meta text-ink-mute">暂无可选模型</p>
          ) : (
            <ul className="max-h-[260px] space-y-0.5 overflow-auto p-1.5">
              {rows.map(row => {
                const index = value.indexOf(row.id);
                const isDefault = index === 0;
                return (
                  <li
                    key={row.id}
                    draggable={row.picked}
                    onDragStart={row.picked ? onDragStart(row.id) : undefined}
                    onDragOver={row.picked ? onDragOver(row.id) : undefined}
                    onDrop={row.picked ? onDrop(row.id) : undefined}
                    onDragEnd={row.picked ? onDragEnd : undefined}
                    className={cn(
                      'flex items-center gap-2 rounded-[7px] px-2 py-1.5 transition-colors',
                      row.picked ? 'bg-primary-soft' : 'hover:bg-panel-soft',
                      overId === row.id && dragId && dragId !== row.id && 'ring-1 ring-primary',
                    )}
                  >
                    <input
                      type="checkbox"
                      className="h-3 w-3 shrink-0 accent-primary"
                      checked={row.picked}
                      onChange={() => toggle(row.id)}
                      aria-label={row.label}
                    />
                    <span className="min-w-0 flex-1 truncate text-xs text-ink">{row.label}</span>
                    {isDefault && (
                      <span className="shrink-0 rounded bg-primary-soft px-1 py-px text-micro text-primary">
                        默认
                      </span>
                    )}
                    {row.picked && index > 0 && (
                      <GripVertical
                        size={12}
                        className="shrink-0 cursor-grab text-ink-mute"
                        aria-label="拖动调整顺序"
                      />
                    )}
                    {row.picked && <Check size={12} className="shrink-0 text-primary" />}
                  </li>
                );
              })}
            </ul>
          )}
          {full && (
            <p className="border-t border-line-soft px-3 py-2 text-micro text-danger">
              最多选 {MAX_ALLOWED_MODELS} 款模型，先取消一款再选
            </p>
          )}
          {selected.length > 1 && (
            <p className="border-t border-line-soft px-3 py-2 text-micro text-ink-mute">
              拖动手柄可调整顺序，第一个是默认模型
            </p>
          )}
        </div>
      )}
    </div>
  );
}
