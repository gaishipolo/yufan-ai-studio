import { useEffect, useMemo, useRef, useState } from 'react';
import { cn } from '@/shared/lib/cn';
import { insertRef } from './specDraft';
import {
  applyMention,
  detectMention,
  filterMentionChips,
  type MentionQuery,
} from './mention';

export interface PromptChip {
  token: string;
  label: string;
  all: boolean;
}

/**
 * 提示词输入框：**支持 `@` 提及槽位**。
 *
 * 两条插入路径完全等价（写进提示词的都是同一个 `{{slot:key}}` token）：
 * - 在框里打 `@` → 弹候选 → 选中（鼠标点 / ↑↓ + Enter）；
 * - 点下方 chip → 插到当前光标处。
 *
 * 光标由本组件自己管：受控 textarea 在 value 变化后会把光标甩到末尾，
 * 插入完必须自己 `setSelectionRange` 放回去。
 */
export function PromptBox({
  value,
  chips,
  onChange,
  onInsert,
}: {
  value: string;
  chips: PromptChip[];
  onChange: (text: string) => void;
  /** 程序写入引用后的回调（chip 按钮 / `@` 提及共用）。 */
  onInsert: (text: string) => void;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const [mention, setMention] = useState<MentionQuery | null>(null);
  const [active, setActive] = useState(0);
  const [query, setQuery] = useState('');
  /* 受控 textarea 在 value 变化后会把光标甩到末尾，所以插入完要自己放回去。
     记一个「下一次渲染后要设的光标」，等 DOM 更新完再 setSelectionRange。 */
  const pendingCaret = useRef<number | null>(null);
  /* 光标位置由本组件自己记着：chip 按钮插入要插到光标处，父组件拿不到。 */
  const caretRef = useRef<number | null>(null);

  /* 候选跟着光标处的 `@查询词` 走。候选列表变了就把高亮项拉回第一项，
     否则按方向键会跳到一个已经不存在的项上。 */
  const candidates = useMemo(
    () => (mention ? filterMentionChips(chips, mention.query) : []),
    [mention, chips],
  );

  useEffect(() => {
    setActive(0);
  }, [candidates.length, mention?.query]);

  useEffect(() => {
    const at = pendingCaret.current;
    if (at === null) return;
    pendingCaret.current = null;
    const node = ref.current;
    if (!node) return;
    node.focus();
    node.setSelectionRange(at, at);
  }, [value]);

  const close = () => {
    setMention(null);
    setQuery('');
  };

  /* 选中候选：把 `@查询词` 整段换成 token，光标停在 token 之后。 */
  const choose = (token: string) => {
    if (!mention) return;
    const result = applyMention(value, mention, token);
    pendingCaret.current = result.caret;
    onInsert(result.text);
    close();
  };

  /* 下方 chip 按钮插入引用：插到当前光标处（`@` 提及是另一条路，两者等价）。 */
  const insertAtCaret = (token: string) => {
    const at = caretRef.current ?? value.length;
    const result = insertRef(value, token, at);
    pendingCaret.current = result.caret;
    onInsert(result.text);
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (candidates.length === 0) return;
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setActive(index => (index + 1) % candidates.length);
      return;
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault();
      setActive(index => (index - 1 + candidates.length) % candidates.length);
      return;
    }
    if (event.key === 'Enter' || event.key === 'Tab') {
      event.preventDefault();
      choose(candidates[active].token);
      return;
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      close();
    }
  };

  return (
    <div>
      {/* 面板的定位基准只包住 textarea：包住整块的话 top-full 会落到
          chip 那一行下面，弹窗跟输入框之间隔着一截，看着不像"就地弹出"。
          `z-20` 让这层浮到 chip 行之上 —— 否则后绘制的 chip 会盖住候选条。 */}
      <div className="relative z-20">
        <textarea
          ref={ref}
          value={value}
          rows={10}
          placeholder="写提示词；打 @ 可以直接选槽位"
          onChange={event => {
            const text = event.target.value;
            const at = event.target.selectionStart ?? 0;
            onChange(text);
            caretRef.current = at;
            // 提及面板由光标位置推导：输入变化后立刻重算，删掉 @ 它就自己收起。
            const hit = detectMention(text, at);
            setMention(hit);
            setQuery(hit?.query ?? '');
          }}
          onSelect={event => {
            const node = event.currentTarget;
            const at = node.selectionStart ?? 0;
            caretRef.current = at;
            /* 用 DOM 里的最新值，不是 props 的 value：选区事件常常紧跟着输入事件
               触发，那一刻 props 还是旧的（少打的那半个字），拿旧值算就会把刚弹出的
               候选面板判成 null —— 面板一闪就没了。 */
            setMention(detectMention(node.value, at));
          }}
          onKeyDown={onKeyDown}
          className={cn(
            'w-full resize-y rounded-xl border px-3 py-2.5 text-xs leading-[1.7] outline-none transition-colors',
            'border-line-soft bg-panel-soft text-ink placeholder:text-[#a9b8bf]',
            'hover:border-line focus:border-primary-line focus:bg-panel focus:ring-2 focus:ring-primary/10',
            mention && 'border-primary-line bg-panel focus:ring-2 focus:ring-primary/10',
          )}
        />

        {/* 提及面板：贴着输入框浮出，跟输入框同宽，像输入法的候选条。 */}
        {mention && candidates.length > 0 && (
          <div
            className={cn(
              'absolute left-0 right-0 top-full z-30 mt-1 max-h-[220px] overflow-y-auto',
              'scroll-thin rounded-xl border border-line bg-panel p-1 shadow-pop',
              'animate-slideDown',
            )}
          >
            <p className="px-2 py-1 text-[10px] text-ink-mute">
              {query ? `匹配「${query}」` : '选择要引用的槽位'}
            </p>
            {candidates.map((chip, index) => (
              <button
                key={chip.token}
                type="button"
                onMouseDown={event => {
                  // mousedown 先于 blur，click 会被输入框失焦吃掉 —— 用 mousedown 选中。
                  event.preventDefault();
                  choose(chip.token);
                }}
                onMouseEnter={() => setActive(index)}
                className={cn(
                  'flex w-full items-center justify-between gap-2 rounded-lg px-2 py-1.5 text-left transition-colors',
                  index === active ? 'bg-primary-soft text-primary' : 'text-ink-soft hover:bg-panel-soft',
                )}
              >
                <span className="truncate text-[12px]">{chip.label}</span>
                <span
                  className={cn(
                    'shrink-0 rounded px-1 text-[9.5px]',
                    chip.all ? 'bg-violet/10 text-violet' : 'bg-panel-soft text-ink-mute',
                  )}
                >
                  {chip.all ? '全部' : '单个'}
                </span>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* chip 行降到 z-0：候选面板浮在上面时，它不该把面板压出一道缺口。
          有了 @ 之后它是**辅助**路径（有人不知道能打 @），所以做小做淡，
          不跟提示词正文抢注意力。 */}
      <div className="relative z-0 mt-2 flex flex-wrap items-center gap-1">
        {chips.length === 0 ? (
          <span className="text-[11px] text-ink-mute">先在左边加槽位，再打 @ 引用</span>
        ) : (
          <>
            <span className="text-[10.5px] text-ink-mute">打 @ 引用</span>
            {chips.map(chip => (
              <button
                key={chip.token}
                type="button"
                title={chip.token}
                onClick={() => insertAtCaret(chip.token)}
                className={cn(
                  'rounded-md px-1.5 py-[1px] text-[10.5px] transition-colors',
                  chip.all
                    ? 'text-violet/80 hover:bg-violet/10 hover:text-violet'
                    : 'text-ink-mute hover:bg-primary-soft hover:text-primary',
                )}
              >
                {chip.label}
              </button>
            ))}
          </>
        )}
      </div>
    </div>
  );
}
