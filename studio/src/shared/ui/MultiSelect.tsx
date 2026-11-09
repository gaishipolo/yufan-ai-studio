import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { Check, ChevronDown } from 'lucide-react';
import { cn } from '@/shared/lib/cn';

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
  disabled?: boolean;
  /**
   * 允许在下拉里**就地新建**一个选项（标签用）。
   *
   * 返回新选项的值就自动选中；返回 `null` 表示没建成（调用方负责提示原因），
   * 输入内容原样保留，用户不用重打一遍。
   *
   * 为什么不传 `onCreate` 就不显示这一行：能不能建是权限决定的
   * （标签要 `library:create`），组件不该替调用方判断，也不该给用不了的人
   * 摆一个必定失败的输入框。
   */
  onCreate?: (name: string) => Promise<string | null>;
  /** 新建行的占位文案，默认「新建并选中」 */
  createPlaceholder?: string;
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
 * 调用方必须把本组件放在**表单顶部**：浮层是 absolute 向下展开的，
 * 放在 `Modal` 滚动容器底部会被 `overflow-auto` 裁切、还盖住提交按钮。
 *
 * 组件只管「选哪些」，不认识角色或小组；选项由调用方从服务端数据映射。
 */
export function MultiSelect({
  options,
  value,
  onChange,
  placeholder = '请选择',
  emptyText = '暂无可选项',
  disabled = false,
  onCreate,
  createPlaceholder = '新建并选中',
  id,
  ariaLabel,
}: MultiSelectProps) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState('');
  const [creating, setCreating] = useState(false);
  /* 本地下沉新建出来的选项。父级重新拉列表要一个来回，这期间收起态会退化成
     显示 id（`t9` 这种）。把新建项记在本地，等 props 回流后按 value 去重合并，
     用户就看不到那一帧闪烁。 */
  const [createdOptions, setCreatedOptions] = useState<MultiSelectOption[]>([]);
  const rootRef = useRef<HTMLDivElement>(null);
  const listId = useId();

  const merged = useMemo(() => {
    if (createdOptions.length === 0) return options;
    const known = new Set(options.map((option) => option.value));
    return [...options, ...createdOptions.filter((option) => !known.has(option.value))];
  }, [options, createdOptions]);

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

  const labelOf = (item: string) =>
    merged.find((option) => option.value === item)?.label ?? item;
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

  /* 新建：回车提交。必须 preventDefault —— 本组件常放在 Modal 的表单里，
     回车会连带提交整个表单。失败了不清空输入，让人改一个字就能重试。 */
  const submitCreate = async () => {
    const name = draft.trim();
    if (!onCreate || !name || creating) return;
    setCreating(true);
    let created: string | null = null;
    try {
      created = await onCreate(name);
    } catch {
      created = null;
    } finally {
      setCreating(false);
    }
    if (!created) return;
    if (!value.includes(created)) onChange([...value, created]);
    setCreatedOptions((previous) =>
      previous.some((option) => option.value === created)
        ? previous
        : [...previous, { value: created, label: name }],
    );
    setDraft('');
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
        onClick={() => setOpen((previous) => !previous)}
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

      {open && (
        <div
          id={listId}
          className="absolute left-0 right-0 top-[calc(100%+4px)] z-30 overflow-hidden rounded-lg border border-line bg-white shadow-pop"
        >
          {merged.length === 0 ? (
            <p className="px-3 py-4 text-center text-[11.5px] text-ink-mute">{emptyText}</p>
          ) : (
            <ul className="max-h-[196px] space-y-0.5 overflow-auto p-1.5">
              {merged.map((option) => {
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
                            <span className="shrink-0 rounded bg-primary-soft px-1 py-px text-[10px] text-primary">
                              {option.badge}
                            </span>
                          )}
                        </span>
                        {option.description && (
                          <span className="truncate text-[11px] text-ink-mute">
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

          {onCreate && !disabled && (
            <div className="flex items-center gap-1.5 border-t border-line-soft p-1.5">
              <input
                type="text"
                value={draft}
                disabled={creating}
                maxLength={24}
                placeholder={createPlaceholder}
                aria-label={createPlaceholder}
                autoComplete="off"
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key !== 'Enter') return;
                  event.preventDefault();
                  void submitCreate();
                }}
                className={cn(
                  'h-7 min-w-0 flex-1 rounded-[7px] border border-line bg-white px-2 text-xs text-ink',
                  'placeholder:text-ink-mute focus:border-primary-line focus:outline-none focus:ring-2 focus:ring-primary/10',
                  'disabled:cursor-not-allowed disabled:bg-panel-soft',
                )}
              />
              <button
                type="button"
                disabled={!draft.trim() || creating}
                onClick={() => void submitCreate()}
                className={cn(
                  'h-7 shrink-0 rounded-[7px] px-2 text-[11.5px] font-medium transition-colors',
                  'bg-primary-soft text-primary hover:bg-primary/15',
                  'disabled:cursor-not-allowed disabled:bg-panel-soft disabled:text-ink-mute',
                )}
              >
                {creating ? '新建中' : '新建'}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
