import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import {cn} from '@/shared/lib/cn';
import {SLOT_TYPE_ICONS} from './slotIcons';
import {insertRef, type SlotType} from './specDraft';
import { CursorMenu, CursorMenuItem, CursorMenuGroup, type CaretPoint } from './CursorMenu';
import { caretPointOf } from './caretPoint';
import {
  applyMention,
  detectMention,
  filterMentionChips,
  type MentionQuery,
} from './mention';

export interface MentionCandidate {
  /** 插进文本里的 token，如 `{{slot:hero}}`。 */
  token: string;
  /** 给人看的名字。 */
  label: string;
  /** 这个候选代表「整组」还是「单张」（画角标用）。 */
  all?: boolean;
  /** 槽位类型：画图标用（图标比「成片/图片」这类文字省地方）。 */
  kind?: SlotType;
  /**
   * 这个槽位**必定有素材**吗（因而它的编号必定存在）？
   *
   * 槽位文案（选中时 / 未选时文案）里只能引用这样的槽位：可选槽位
   * 可能没人填，那时没有编号可指。与后端
   * `core/template_spec/rules.py::is_always_filled` 同一口径。
   */
  alwaysMaterial?: boolean;
}

/** 外部能对本组件做的事。 */
export interface MentionTextareaHandle {
  /** 在当前光标处插入一个 token，并把光标放到它后面。 */
  insertToken: (token: string) => void;
}

/**
 * **会唤起 `@` 候选的多行输入框**。
 *
 * 提示词框与槽位的「选中时文案」框用的是同一套交互：打 `@` → 弹候选 →
 * 选中（鼠标点 / ↑↓ + Enter）。两处各写一遍的话，候选面板的行为会漂移
 * （一边能键盘选、一边不能；一边的面板会被视口切掉、另一边不会）。
 *
 * ## 为什么不让用户手打 `{{slot:key}}`
 *
 * 手打 token 有两个错，而两个错**都不报错**：
 * - key 拼错 → 编译期才 400（作者已经填完素材了）；
 * - 槽位改名 → 引用静默失效（改名与引用是两处事实，改一处漏一处）。
 *
 * 所以候选是**算出来**的（`chips` 由调用方给出），用户只管选。
 *
 * ## 受控 textarea 的光标
 *
 * 受控 textarea 在 value 变化后会把光标甩到末尾，插入完必须自己
 * `setSelectionRange` 放回去 —— 所以本组件自己记着「下一次渲染后要设的光标」。
 */
export const MentionTextarea = forwardRef<
  MentionTextareaHandle,
  {
    value: string;
    candidates: MentionCandidate[];
    onChange: (text: string) => void;
    /** 程序写入 token 后的回调（候选选中时走这条）。 */
    onInsert: (text: string) => void;
    placeholder?: string;
    rows?: number;
    maxLength?: number;
    className?: string;
    id?: string;
    'aria-label'?: string;
    /** 候选面板顶部的说明文案。 */
    panelHint?: string;
    /** 框下面的额外内容（预览等）。 */
    children?: ReactNode;
  }
>(function MentionTextarea(
  {
    value,
    candidates,
    onChange,
    onInsert,
    placeholder,
    rows = 2,
    maxLength,
    className,
    id,
    'aria-label': ariaLabel,
    panelHint,
    children,
  },
  ref,
) {
  const nodeRef = useRef<HTMLTextAreaElement>(null);
  const [mention, setMention] = useState<MentionQuery | null>(null);
  const [caretPoint, setCaretPoint] = useState<CaretPoint | null>(null);
  const updateMention = (next: MentionQuery | null) => {
    setCaretPoint(next ? caretPointOf(nodeRef.current) : null);
    setMention(next);
  };
  const [active, setActive] = useState(0);
  /* 受控 textarea 在 value 变化后会把光标甩到末尾，所以插入完要自己放回去。 */
  const pendingCaret = useRef<number | null>(null);
  /* 光标位置本组件自己记着：外部的「插入 token」按钮要插到光标处，父组件拿不到。 */
  const caretRef = useRef<number | null>(null);

  /* 候选跟着光标处的 `@查询词` 走。候选列表变了就把高亮项拉回第一项，
     否则按方向键会跳到一个已经不存在的项上。 */
  const matches = useMemo(
    () => (mention ? filterMentionChips(candidates, mention.query) : []),
    [mention, candidates],
  );

  useEffect(() => {
    setActive(0);
  }, [matches.length, mention?.query]);

  useEffect(() => {
    const at = pendingCaret.current;
    if (at === null) return;
    pendingCaret.current = null;
    const node = nodeRef.current;
    if (!node) return;
    node.focus();
    node.setSelectionRange(at, at);
  }, [value]);

  /* 外部（下方 chip 按钮）要求在光标处插入 token。 */
  useImperativeHandle(
    ref,
    () => ({
      insertToken: (token: string) => {
        const at = caretRef.current ?? value.length;
        const result = insertRef(value, token, at);
        pendingCaret.current = result.caret;
        onInsert(result.text);
      },
    }),
    [value, onInsert],
  );

  const close = () => updateMention(null);

  /* 选中候选：把 `@查询词` 整段换成 token，光标停在 token 之后。 */
  const choose = (token: string) => {
    if (!mention) return;
    const result = applyMention(value, mention, token);
    pendingCaret.current = result.caret;
    onInsert(result.text);
    close();
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (matches.length === 0) return;
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setActive(index => (index + 1) % matches.length);
      return;
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault();
      setActive(index => (index - 1 + matches.length) % matches.length);
      return;
    }
    if (event.key === 'Enter' || event.key === 'Tab') {
      event.preventDefault();
      choose(matches[active].token);
      return;
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      close();
    }
  };

  const open = mention !== null && matches.length > 0;

  return (
    <div>
      {/* 面板的定位基准只包住 textarea：包住整块的话 top-full 会落到
          下面一行下面，弹窗跟输入框之间隔着一截，看着不像"就地弹出"。 */}
      <div className="relative z-20">
        <textarea
          ref={nodeRef}
          id={id}
          aria-label={ariaLabel}
          value={value}
          rows={rows}
          maxLength={maxLength}
          placeholder={placeholder}
          onChange={event => {
            const text = event.target.value;
            const at = event.target.selectionStart ?? 0;
            caretRef.current = at;
            onChange(text);
            // 提及面板由光标位置推导：输入变化后立刻重算，删掉 @ 它就自己收起。
            updateMention(detectMention(text, at));
          }}
          onSelect={event => {
            const node = event.currentTarget;
            const at = node.selectionStart ?? 0;
            caretRef.current = at;
            /* 用 DOM 里的最新值，不是 props 的 value：选区事件常常紧跟着输入事件
               触发，那一刻 props 还是旧的（少打的那半个字），拿旧值算就会把刚弹出的
               候选面板判成 null —— 面板一闪就没了。 */
            updateMention(detectMention(node.value, at));
          }}
          onKeyDown={onKeyDown}
          className={cn(
            'w-full resize-y rounded-lg border border-line bg-panel px-2.5 py-2 text-xs leading-[1.7] text-ink outline-none transition-colors placeholder:text-ink-mute',
            'hover:border-line-strong focus:border-primary-line focus:ring-2 focus:ring-primary/10',
            open && 'border-primary-line bg-panel focus:ring-2 focus:ring-primary/10',
            className,
          )}
        />

      </div>
      <CursorMenu point={open ? caretPoint : null} onDismiss={close}>
        <CursorMenuGroup
          label={panelHint ?? (mention?.query ? `匹配「${mention.query}」` : '选择要引用的槽位')}
        >
          {matches.map((chip, index) => {
            const Icon = chip.kind ? SLOT_TYPE_ICONS[chip.kind] : undefined;
            return (
              <CursorMenuItem
                key={chip.token}
                label={chip.label}
                hint={chip.all ? '全部' : '单个'}
                icon={Icon && <Icon size={12} />}
                active={index === active}
                onSelect={() => choose(chip.token)}
                onHover={() => setActive(index)}
              />
            );
          })}
        </CursorMenuGroup>
      </CursorMenu>
      {children}
    </div>
  );
});
