import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from 'react';
import { cn } from '@/shared/lib/cn';
import { useDropPlacement } from '@/shared/hooks/useDropPlacement';
import { SLOT_TYPE_ICONS } from './slotIcons';
import { insertRef } from './specDraft';
import {
  applyMention,
  detectMention,
  filterMentionChips,
  type MentionQuery,
} from './mention';
import type { MentionCandidate, MentionTextareaHandle } from './MentionTextarea';

/**
 * 正文里的素材占位。**两边允许空格**（`{{ slot:key }}`）：后端与 `specDraft`
 * 都是容空格版本，这里写成严格版就会漏掉老草稿里的带空格引用。
 */
const TOKEN_RE = /\{\{\s*slot:[a-z][a-z0-9_]{0,39}(?:\*|\.\d+)?\s*\}\}/g;

/** 去掉空白后的 token：正文里的写法可能带空格，候选里的是规范形式。 */
const normalizeToken = (raw: string) => raw.replace(/\s+/g, '');

/**
 * **把素材占位渲染成胶囊的多行输入框**（`contenteditable`）。
 *
 * ## 为什么不用 `<textarea>`
 *
 * textarea 里只能是纯文本，作者看到的是 `{{slot:image_1}}` —— 内部 id，
 * 既读不懂、也看不出「这里会被一张素材顶掉」。换成 `contenteditable` 之后，
 * 同一个位置画成一枚带类型图标的胶囊，与下面那排 chip 同一种语言。
 *
 * ## 为什么是「非受控」
 *
 * React 管 `contenteditable` 的 children 会出两类事故：
 * - 每次 `value` 变都重建 DOM → 光标被甩回开头，中文输入法正在组字时还会丢字；
 * - 反过来不管 DOM，外部写入（点 chip 插入 / 选 @ 候选）又落不到界面上。
 *
 * 所以这里 React **不渲染任何子节点**，只在 `value` 与 DOM 的实际内容不一致时
 * 手动重建一次，并把光标放回它该在的地方（`pendingCaret`）。
 *
 * ## 中文输入法
 *
 * 组字期间（`compositionstart` → `compositionend`）**不同步**：那时候 DOM 里
 * 是拼音、`value` 里是上一次的成文，一重建就把拼音吞了。`isComposing` 也要看，
 * Chrome 的 `input` 事件在组字中途照样会来。
 */

/** 序列化：DOM → 带 token 的纯文本。这是「界面上是什么」的唯一真相来源。 */
function serialize(root: HTMLElement): string {
  let out = '';
  for (const node of Array.from(root.childNodes)) {
    if (node.nodeType === Node.TEXT_NODE) {
      out += node.textContent ?? '';
    } else if (node instanceof HTMLElement && node.dataset.token) {
      out += node.dataset.token;
    } else if (node instanceof HTMLElement) {
      // 浏览器有时会自己包一层（粘贴、输入法），递归下去
      out += serialize(node);
    }
  }
  return out;
}

/** 一串内容在「文本偏移」上占多长：chip 按它的 token 长度算，不按显示名。 */
function lengthOf(node: Node): number {
  if (node.nodeType === Node.TEXT_NODE) return (node.textContent ?? '').length;
  if (node instanceof HTMLElement) {
    if (node.dataset.token) return node.dataset.token.length;
    return Array.from(node.childNodes).reduce((sum, child) => sum + lengthOf(child), 0);
  }
  return 0;
}

/** 某个 DOM 点在**文本偏移**上落在哪；不在 `root` 里返回 `null`。 */
function pointOffset(root: HTMLElement, container: Node, offset: number): number | null {
  if (!root.contains(container)) return null;

  let acc = 0;
  let found = -1;
  const visit = (node: Node): boolean => {
    if (node === container) {
      if (node.nodeType === Node.TEXT_NODE) {
        acc += Math.min(offset, (node.textContent ?? '').length);
      } else {
        for (let i = 0; i < offset; i += 1) {
          const child = node.childNodes[i];
          if (child) acc += lengthOf(child);
        }
      }
      found = acc;
      return true;
    }
    if (node.nodeType === Node.TEXT_NODE || (node instanceof HTMLElement && node.dataset.token)) {
      acc += lengthOf(node);
      return false;
    }
    for (const child of Array.from(node.childNodes)) {
      if (visit(child)) return true;
    }
    return false;
  };
  for (const child of Array.from(root.childNodes)) {
    if (visit(child)) break;
  }
  return found >= 0 ? found : null;
}

/**
 * 整条选区在**文本偏移**上的起止。
 *
 * 粘贴要知道「从哪到哪」：光标处插入是 `start === end`，选中一段再粘是替换。
 */
function selectionOffsets(root: HTMLElement): { start: number; end: number } | null {
  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0) return null;
  const range = selection.getRangeAt(0);
  const start = pointOffset(root, range.startContainer, range.startOffset);
  const end = pointOffset(root, range.endContainer, range.endOffset);
  if (start === null || end === null) return null;
  return { start, end };
}

/** 当前光标在**文本偏移**上的位置。 */
function caretOffset(root: HTMLElement): number {
  return selectionOffsets(root)?.end ?? 0;
}

/** 两段文字从头开始有多长是一样的 —— 也就是「这次改动从哪儿起头」。 */
function commonPrefix(a: string, b: string): number {
  let index = 0;
  while (index < a.length && index < b.length && a[index] === b[index]) index += 1;
  return index;
}

/** 把光标放到「文本偏移」处。落在胶囊内部时放到它后面。 */
function setCaret(root: HTMLElement, offset: number): void {
  const selection = window.getSelection();
  if (!selection) return;

  let acc = 0;
  let point: { node: Node; offset: number } | null = null;

  const visit = (node: Node): boolean => {
    if (node.nodeType === Node.TEXT_NODE) {
      const len = (node.textContent ?? '').length;
      if (acc + len >= offset) {
        point = { node, offset: Math.max(0, offset - acc) };
        return true;
      }
      acc += len;
      return false;
    }
    if (node instanceof HTMLElement && node.dataset.token) {
      const len = node.dataset.token.length;
      if (acc + len >= offset) {
        /* 光标不该落进胶囊里（它是 contenteditable=false）：
           放到这枚胶囊**之后**。 */
        const parent = node.parentNode ?? root;
        point = { node: parent, offset: Array.from(parent.childNodes).indexOf(node) + 1 };
        return true;
      }
      acc += len;
      return false;
    }
    for (const child of Array.from(node.childNodes)) {
      if (visit(child)) return true;
    }
    return false;
  };
  for (const child of Array.from(root.childNodes)) {
    if (visit(child)) break;
  }

  const range = document.createRange();
  if (point) {
    const { node, offset: at } = point as { node: Node; offset: number };
    const limit = node.nodeType === Node.TEXT_NODE ? (node.textContent ?? '').length : node.childNodes.length;
    range.setStart(node, Math.min(at, limit));
    range.collapse(true);
  } else {
    range.selectNodeContents(root);
    range.collapse(false);
  }
  selection.removeAllRanges();
  selection.addRange(range);
}

export interface RichPromptEditorProps {
  value: string;
  candidates: MentionCandidate[];
  /**
   * 用户改动。返回 `false` = 这次写入**被拒**（父组件对引用有校验），
   * 编辑器会把 DOM 对齐回 `value`（见 `syncFromDom`）。
   */
  onChange: (text: string) => void | boolean;
  /** 程序写入（`@` 候选选中 / 粘贴）后的回调；返回值同上。 */
  onInsert: (text: string) => void | boolean;
  placeholder?: string;
  className?: string;
  id?: string;
  'aria-label'?: string;
  panelHint?: string;
}

export const RichPromptEditor = forwardRef<MentionTextareaHandle, RichPromptEditorProps>(
  function RichPromptEditor(
    { value, candidates, onChange, onInsert, placeholder, className, id, 'aria-label': ariaLabel, panelHint },
    ref,
  ) {
    const rootRef = useRef<HTMLDivElement>(null);
    const [mention, setMention] = useState<MentionQuery | null>(null);
    const [active, setActive] = useState(0);
    /** 组字中：这期间不重渲染 DOM，否则拼音会被吞掉 */
    const composing = useRef(false);
    /** 重建 DOM 之后要把光标放回的文本偏移 */
    const pendingCaret = useRef<number | null>(null);
    /** 外部「插入 token」要插到光标处，父组件拿不到，这里记着 */
    const caretRef = useRef(0);

    const matches = useMemo(
      () => (mention ? filterMentionChips(candidates, mention.query) : []),
      [mention, candidates],
    );
    const open = mention !== null && matches.length > 0;

    useEffect(() => {
      setActive(0);
    }, [matches.length, mention?.query]);

    const chipOf = (token: string) =>
      candidates.find(candidate => normalizeToken(candidate.token) === normalizeToken(token));

    /* 重建 DOM：text 节点 + 胶囊交替。React 不管这些子节点。 */
    const paint = (root: HTMLElement, text: string) => {
      root.textContent = '';
      let last = 0;
      for (const match of text.matchAll(TOKEN_RE)) {
        const at = match.index ?? 0;
        if (at > last) root.appendChild(document.createTextNode(text.slice(last, at)));
        root.appendChild(capsule(match[0], chipOf(match[0])));
        last = at + match[0].length;
      }
      if (last < text.length) root.appendChild(document.createTextNode(text.slice(last)));
    };

    const capsule = (token: string, candidate?: MentionCandidate) => {
      const span = document.createElement('span');
      /* 光标不能进胶囊：`contenteditable=false` 让浏览器把它当一个整体，
         退格一次删掉整枚，而不是从名字中间逐字删。 */
      span.setAttribute('contenteditable', 'false');
      span.dataset.token = token;
      span.className = cn(
        'mx-0.5 inline-flex translate-y-[1px] cursor-default items-center rounded-[5px] border px-1.5 py-px align-baseline text-[0.95em] font-medium',
        candidate?.all
          ? 'border-violet/30 bg-violet/10 text-violet'
          : 'border-primary-line bg-primary-soft text-primary-deep',
      );
      span.textContent = candidate?.label ?? token;
      return span;
    };

    /* 同步：值变了、或者 DOM 里还有没胶囊化的 token（用户手打出来的）才重建。 */
    useEffect(() => {
      const root = rootRef.current;
      if (!root || composing.current) return;

      const painted = root.querySelectorAll('[data-token]').length;
      const expected = (value.match(TOKEN_RE) ?? []).length;
      if (serialize(root) === value && painted === expected) return;

      const caret = pendingCaret.current;
      pendingCaret.current = null;
      paint(root, value);
      caretRef.current = caret ?? Math.min(caretRef.current, value.length);
      setCaret(root, caret ?? value.length);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [value, candidates]);

    /*
     * 两条写入路径**必须分开**，不能都走 `onChange`：
     *
     * - `onChange` = 用户手打。父组件对它有校验（手写 `{{...}}` 会被拦下
     *   并给红字提示：手写的 key 拼错要等到编译期才 400，手写 `{{Mixed N}}`
     *   还会和填充量错位）。
     * - `onInsert` = 程序插入（点 chip / 选 @ 候选）。token 本来就带花括号，
     *   走校验会被当成「手打」拦掉 —— 表现就是点一下 chip 弹一句
     *   「不要手写大括号」。真踩过。
     */
    const insert = (text: string, caret: number) => {
      pendingCaret.current = caret;
      caretRef.current = caret;
      // 被拒就当没发生：不清掉待用的光标位，下一次重建会用错位置
      if (onInsert(text) === false) pendingCaret.current = null;
    };

    useImperativeHandle(
      ref,
      () => ({
        insertToken: (token: string) => {
          const at = caretRef.current ?? value.length;
          const result = insertRef(value, token, at);
          insert(result.text, result.caret);
        },
      }),
      // eslint-disable-next-line react-hooks/exhaustive-deps
      [value, onInsert],
    );

    const close = () => setMention(null);

    const choose = (token: string) => {
      if (!mention) return;
      const result = applyMention(value, mention, token);
      insert(result.text, result.caret);
      close();
    };

    const syncFromDom = () => {
      const root = rootRef.current;
      if (!root) return;
      const at = caretOffset(root);
      caretRef.current = at;
      const text = serialize(root);
      if (onChange(text) === false) {
        /* 写入被拒（父组件的引用校验）：DOM 里那些字必须撤掉。
           不撤的话「看见的」和「会保存的」不是一回事 —— 更糟的是 `value`
           没变，下面那个重建 effect 不会触发，脏 DOM 就一直挂在那儿。

           光标放在**两段文字的公共前缀末尾** = 这次改动开始的位置：
           打了半个 token 被拒时，光标不会莫名跳到文末。 */
        const back = commonPrefix(text, value);
        paint(root, value);
        caretRef.current = back;
        setCaret(root, back);
        setMention(detectMention(value, back));
        return;
      }
      // 提及面板由光标位置推导：删掉 `@` 它就自己收起
      setMention(detectMention(text, at));
    };

    const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
      if (!open) return;
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

    /* 粘贴只收纯文本，而且**走 `value` 而不是往 DOM 里塞节点**。
       两条理由：
       - 默认粘贴会把外部 HTML（字体、颜色、div）一起带进来，与「一行行提示词」
         的排版冲突，还会搅乱胶囊的 DOM 结构；
       - 往 DOM 塞文本节点后，序列化结果和粘贴内容一样，所以「值变了没有」看
         不出来 —— **胶囊化要等 `value` 变化触发重建**。走 `value` 这条路，
         整段文字和它的胶囊是同一次渲染出来的，没有「半截原文」的中间态：
         要么整段进去（`{{slot:key}}` 直接成胶囊），要么一点不动。
         以前粘一段正确的 `{{slot:image_1}}` 不变胶囊，就是这个中间态。 */
    const onPaste = (event: React.ClipboardEvent<HTMLDivElement>) => {
      event.preventDefault();
      const pasted = event.clipboardData.getData('text/plain');
      if (!pasted) return;
      const root = rootRef.current;
      if (!root) return;
      // 选中一段再粘 = 替换；光标处粘 = 插入
      const range = selectionOffsets(root) ?? { start: value.length, end: value.length };
      const caret = range.start + pasted.length;
      insert(value.slice(0, range.start) + pasted + value.slice(range.end), caret);
    };

    /* 候选面板方向：**量出来**。写死朝下会掉出视口。 */
    const anchorRef = useRef<HTMLDivElement>(null);
    const popupRef = useRef<HTMLDivElement>(null);
    const { side, space } = useDropPlacement(anchorRef, popupRef, open);
    const maxHeight = Math.max(140, Math.min(220, space));

    return (
      <div>
        <div ref={anchorRef} className="relative z-20">
          <div
            ref={rootRef}
            id={id}
            role="textbox"
            aria-label={ariaLabel}
            aria-multiline="true"
            tabIndex={0}
            contentEditable
            suppressContentEditableWarning
            data-placeholder={placeholder}
            onInput={event => {
              /* 组字中途不提交：那时候 DOM 里是拼音，序列化出来是半成品，
                 父组件拿它去重渲染会把正在打的字弄丢。 */
              if (composing.current || (event.nativeEvent as InputEvent).isComposing) return;
              syncFromDom();
            }}
            onCompositionStart={() => {
              composing.current = true;
            }}
            onCompositionEnd={() => {
              composing.current = false;
              syncFromDom();
            }}
            onKeyDown={onKeyDown}
            onPaste={onPaste}
            onBlur={() => {
              const root = rootRef.current;
              if (root) caretRef.current = caretOffset(root);
              close();
            }}
            className={cn(
              'w-full overflow-y-auto whitespace-pre-wrap break-words rounded-lg border border-line bg-white px-2.5 py-2 text-xs leading-[1.7] text-ink outline-none transition-colors',
              'hover:border-line-strong focus:border-primary-line focus:ring-2 focus:ring-primary/10',
              /* **固定高度**，内容多了在框里滚。
                 之前只写了 `min-h` + `overflow-y-auto`：没有高度上限时
                 overflow 不生效，写长的提示词会把整页往下顶，
                 下面的 chip 行、字数、展示案例全被挤走。
                 `resize-y` 留着：想临时看全文可以自己拖大。 */
              'h-[240px] resize-y',
              open && 'border-primary-line bg-panel',
              className,
            )}
          />
          {/* 占位符单独渲染：`contenteditable` 里的 `:empty` 不可靠 ——
              用户删空之后浏览器常留一个 `<br>`，`:empty` 就不成立了。 */}
          {!value && placeholder && (
            <span className="pointer-events-none absolute inset-x-2.5 top-2 text-xs leading-[1.7] text-[#a9b8bf]">
              {placeholder}
            </span>
          )}
          {open && (
            <div
              ref={popupRef}
              data-placement={side}
              style={{ maxHeight }}
              className={cn(
                'absolute left-0 right-0 z-30 overflow-y-auto scroll-thin rounded-xl border border-line bg-panel p-1 shadow-pop',
                side === 'top'
                  ? 'bottom-[calc(100%+4px)] animate-slideUp'
                  : 'top-[calc(100%+4px)] animate-slideDown',
              )}
            >
              <p className="px-2 py-1 text-micro text-ink-mute">
                {panelHint ??
                  (mention?.query ? `匹配「${mention.query}」` : '选择要引用的槽位')}
              </p>
              {matches.map((chip, index) => {
                const Icon = chip.kind ? SLOT_TYPE_ICONS[chip.kind] : undefined;
                return (
                  <button
                    key={chip.token}
                    type="button"
                    onMouseDown={event => {
                      // mousedown 先于 blur，click 会被输入框失焦吃掉
                      event.preventDefault();
                      choose(chip.token);
                    }}
                    onMouseEnter={() => setActive(index)}
                    className={cn(
                      'flex w-full items-center justify-between gap-2 rounded-lg px-2 py-1.5 text-left transition-colors',
                      index === active
                        ? 'bg-primary-soft text-primary'
                        : 'text-ink-soft hover:bg-panel-soft',
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
      </div>
    );
  },
);
