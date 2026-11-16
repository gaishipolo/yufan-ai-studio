import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { cn } from '@/shared/lib/cn';
import { useDropPlacement } from '@/shared/hooks/useDropPlacement';
import { insertRef, type SlotType } from './specDraft';
import { SLOT_TYPE_ICONS } from './slotIcons';
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
  /** 槽位类型：画图标用 */
  kind?: SlotType;
}

/** 正文里的素材占位长什么样：`{{slot:key}}`（键是内部 id，作者看不懂） */
const SLOT_TOKEN = /\{\{slot:[^{}]+\}\}/g;

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

  /* 提示词预览：**正文里的素材占位显示成「插槽名」**，与普通文字分开。

     textarea 里只能是原始 token（`{{slot:image_1}}` 这种内部 id，作者读不懂），
     而这一段是「这段话长什么样」的读法：占位是一枚带类型图标的 chip，普通文字是纯文字。
     看一眼就知道哪几段会被素材顶掉。 */
  const preview = useMemo(() => {
    const parts: ReactNode[] = [];
    let last = 0;
    for (const match of value.matchAll(SLOT_TOKEN)) {
      const at = match.index ?? 0;
      if (at > last) parts.push(value.slice(last, at));
      const token = match[0];
      const chip = chips.find(item => item.token === token);
      const Icon = chip?.kind ? SLOT_TYPE_ICONS[chip.kind] : undefined;
      parts.push(
        <span
          key={`${token}-${at}`}
          title={token}
          className={cn(
            'mx-0.5 inline-flex translate-y-[1px] items-center gap-1 rounded-[5px] border px-1.5 py-px align-baseline text-[0.95em] font-medium',
            chip?.all
              ? 'border-violet/30 bg-violet/10 text-violet'
              : 'border-primary-line bg-primary-soft text-primary-deep',
          )}
        >
          {Icon && <Icon size={11} className="shrink-0" />}
          {chip?.label ?? token}
        </span>,
      );
      last = at + token.length;
    }
    if (last < value.length) parts.push(value.slice(last));
    return parts;
  }, [value, chips]);

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

  /* 候选面板的方向：**量出来**。模板封装页的提示词框在右栏最下面，
     写死朝下展开的话整块掉到视口外 —— 用户只看到输入框闪一下，
     什么也没出现（这正是用户报的这个问题）。空间不够就朝上弹。 */
  const anchorRef = useRef<HTMLDivElement>(null);
  const popupRef = useRef<HTMLDivElement>(null);
  const mentionOpen = mention !== null && candidates.length > 0;
  const { side, space } = useDropPlacement(anchorRef, popupRef, mentionOpen);
  /* 面板封顶：留出的空间比 220 还少时按空间封顶，否则它自己会溢出视口那一侧。
     下限 140：再窄就没法用了，宁可让它溢出也比压成一条缝强。 */
  const maxHeight = Math.max(140, Math.min(220, space));

  return (
    <div>
      {/* 面板的定位基准只包住 textarea：包住整块的话 top-full 会落到
          chip 那一行下面，弹窗跟输入框之间隔着一截，看着不像"就地弹出"。
          `z-20` 让这层浮到 chip 行之上 —— 否则后绘制的 chip 会盖住候选条。 */}
      <div ref={anchorRef} className="relative z-20">
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

        {/* 提及面板：贴着输入框浮出，跟输入框同宽，像输入法的候选条。
            空间不够就翻到输入框上方 —— 写死朝下会掉出视口。 */}
        {mentionOpen && (
          <div
            ref={popupRef}
            data-placement={side}
            style={{ maxHeight }}
            className={cn(
              'absolute left-0 right-0 z-30 overflow-y-auto scroll-thin rounded-xl border border-line bg-panel p-1 shadow-pop',
              side === 'top' ? 'bottom-[calc(100%+4px)] animate-slideUp' : 'top-[calc(100%+4px)] animate-slideDown',
            )}
          >
            <p className="px-2 py-1 text-micro text-ink-mute">
              {query ? `匹配「${query}」` : '选择要引用的槽位'}
            </p>
            {candidates.map((chip, index) => {
              const Icon = chip.kind ? SLOT_TYPE_ICONS[chip.kind] : undefined;
              return (
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
                  <span className="flex min-w-0 items-center gap-1.5">
                    {Icon && <Icon size={12} className="shrink-0" />}
                    <span className="truncate text-xs">{chip.label}</span>
                  </span>
                  <span
                    className={cn(
                      'shrink-0 rounded px-1 text-micro',
                      chip.all ? 'bg-violet/10 text-violet' : 'bg-panel-soft text-ink-mute',
                    )}
                  >
                    {chip.all ? '全部' : '单个'}
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* 预览：作者写的是 token，看到的是插槽名 */}
      <div data-testid="prompt-preview" className="mt-2 rounded-xl border border-line-soft bg-panel-soft/60 px-3 py-2">
        <div className="mb-1 text-micro text-ink-mute">预览（素材占位显示插槽名）</div>
        <div className="max-h-[110px] overflow-y-auto scroll-thin text-xs leading-[1.9] text-ink-soft">
          {value.trim() ? (
            preview
          ) : (
            <span className="text-ink-mute">还没有提示词</span>
          )}
        </div>
      </div>

      {/* chip 行降到 z-0：候选面板浮在上面时，它不该把面板压出一道缺口。
          有了 @ 之后它是**辅助**路径（有人不知道能打 @），所以做小做淡，
          不跟提示词正文抢注意力。
          「打 @ 引用」这行说明删掉了：输入框的占位符里已经写着
          「打 @ 可以直接选槽位」，再说一遍就是同一句话讲两遍。 */}
      <div className="relative z-0 mt-2 flex flex-wrap items-center gap-1.5">
        {chips.length === 0 ? (
          <span className="text-meta text-ink-mute">先在左边加槽位，再打 @ 引用</span>
        ) : (
          chips.map(chip => {
            const Icon = chip.kind ? SLOT_TYPE_ICONS[chip.kind] : undefined;
            return (
              <button
                key={chip.token}
                type="button"
                title={`插入 ${chip.token}（点一下插到光标处）`}
                onClick={() => insertAtCaret(chip.token)}
                className={cn(
                  'inline-flex items-center gap-1 rounded-lg border px-1.5 py-0.5 text-micro transition-colors',
                  chip.all
                    ? 'border-violet/25 bg-violet/10 text-violet hover:bg-violet/15'
                    : 'border-line-soft bg-panel-soft text-ink-soft hover:border-primary-line hover:text-primary',
                )}
              >
                {Icon && <Icon size={11} />}
                {chip.label}
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}
