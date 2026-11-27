import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from 'react';
import { cn } from '@/shared/lib/cn';
import { SLOT_TYPE_ICONS } from './slotIcons';
import { insertRef } from './specDraft';
import {
  applyMention,
  detectMention,
  filterMentionChips,
  type MentionQuery,
} from './mention';
import type { MentionCandidate, MentionTextareaHandle } from './MentionTextarea';
import {parseAnchors} from './defaultText';
import { CursorMenu, CursorMenuItem, CursorMenuGroup, type CaretPoint } from './CursorMenu';
import { caretPointOf, selectionEndPoint } from './caretPoint';
/* 正则与计数拆到 capsuleCount.ts：fast-refresh 要求组件文件只导出组件。 */
import { expectedCapsuleCount, TOKEN_RE } from './capsuleCount';

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

/**
 * 序列化：DOM → 带 token 与锚点的纯文本。这是「界面上是什么」的唯一真相来源。
 *
 * **锚点标记在这里被重建**：DOM 里默认描述段显示的是高亮文字（没有标记），
 * 但 `value` 里必须留着 `⟦e:key⟧…⟦/e⟧` —— 它记着「这段归哪个槽位」，
 * 作者改字后靠它把位置找回来。
 */
/**
 * 光标缓冲区：高亮段首尾各放一个只含零宽字符的文本节点。
 *
 * 没有它时，若高亮段是正文第一段/最后一段，浏览器找不到段外的文本节点放光标，
 * 会把光标塞进高亮段里 —— 作者在段首打「前缀」，字却变成了默认描述的一部分
 * （高亮跟着变大），而不是普通正文。零宽节点给光标一个**段外**的落点。
 *
 * 序列化时只剥**独占**的零宽字符，作者真的打进来的字照常保留。
 */
const CARET_BUFFER = '\u200b';

/** 缓冲区节点：只含零宽字符、带标记。作者真的在里面打字时它仍有内容。 */
function isBuffer(node: Node): boolean {
  return node instanceof HTMLElement && node.dataset.caretBuffer === '';
}

function serializeText(text: string): string {
  return text.replaceAll(CARET_BUFFER, '');
}

function serialize(root: HTMLElement): string {
  let out = '';
  for (const node of Array.from(root.childNodes)) {
    /* 缓冲区整体跳过它自己的零宽字符；作者打进来的普通字照常收。 */
    if (isBuffer(node)) {
      out += serializeText(node.textContent ?? '');
    } else if (node.nodeType === Node.TEXT_NODE) {
      out += serializeText(node.textContent ?? '');
    } else if (node instanceof HTMLElement && node.dataset.token) {
      out += node.dataset.token;
    } else if (node instanceof HTMLElement && node.dataset.anchor) {
      // 默认描述段：包回锚点标记，中间是作者写的字。
      out += `⟦e:${node.dataset.anchor}⟧${serialize(node)}⟦/e⟧`;
    } else if (node instanceof HTMLElement) {
      // 浏览器有时会自己包一层（粘贴、输入法），递归下去
      out += serialize(node);
    }
  }
  return out;
}

/** 一串内容在「文本偏移」上占多长：chip 按它的 token 长度算，不按显示名。 */
function lengthOf(node: Node): number {
  /* 缓冲区在文本偏移上占 0：作者的操作坐标里不该有这个内部节点。 */
  if (isBuffer(node)) return 0;
  if (node.nodeType === Node.TEXT_NODE) return serializeText(node.textContent ?? '').length;
  if (node instanceof HTMLElement) {
    if (node.dataset.token) return node.dataset.token.length;
    /* 锚点段：文本偏移上占「标记 + 内容 + 标记」——
       `value` 里锚点占着位置，DOM 里没有，偏移换算必须把它算进去，
       否则光标位置会随着锚点数量漂移。 */
    if (node.dataset.anchor) {
      const open = `⟦e:${node.dataset.anchor}⟧`.length;
      const close = '⟦/e⟧'.length;
      return (
        open +
        close +
        Array.from(node.childNodes).reduce((sum, child) => sum + lengthOf(child), 0)
      );
    }
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
    /* 锚点段：`value` 里标记占着位置，DOM 里没有。
       进去之前加开始标记的长度，出来之后加结束标记的长度 ——
       否则同一段文字在有锚点与无锚点两种正文里偏移不一样。 */
    const anchor = node instanceof HTMLElement ? node.dataset.anchor : undefined;
    if (anchor) acc += `⟦e:${anchor}⟧`.length;

    if (node === container) {
      if (node.nodeType === Node.TEXT_NODE) {
        /* 缓冲区里的光标折算成 0（它前面的字已在别处计过），
           这样「在段首缓冲区打字」得到的位置正好在锚点之前。 */
        if (node.parentElement && isBuffer(node.parentElement)) {
          /* 缓冲区首字符是零宽（序列化时不算），之后作者打进来的字才算偏移。
             光标在 `@` 后面时必须报出那个 `@` 的位置，否则 @ 候选检测失效。 */
          const visible = Math.max(0, Math.min(offset, (node.textContent ?? '').length) - 1);
          found = acc + visible;
          return true;
        }
        acc += Math.min(offset, serializeText(node.textContent ?? '').length);
      } else if (node instanceof HTMLElement && node.dataset.token) {
        /* 胶囊是**一整枚**：边界落在它上面时，offset 1 = 它的**末尾**。
           这里必须按 token 长度算（lengthOf 的口径 = data-token 的长度）——
           按子节点（显示名）长度算会把选区截短，「主图 {{Mixed 1}}」被切成
           「主图 {{M」+「ixed 1}}」，一半进默认描述、一半留在正文。 */
        acc += offset > 0 ? lengthOf(node) : 0;
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
    let hit = false;
    for (const child of Array.from(node.childNodes)) {
      if (visit(child)) {
        hit = true;
        break;
      }
    }
    // 没在里面找到：这个锚点整段算过去了（标记 + 内容 + 标记）。
    if (anchor && !hit) acc += '⟦/e⟧'.length;
    return hit;
  };
  /* 从 root **自己**开始走：选区边界可能就落在 root 上 —— 用户框选到正文
     末尾时，浏览器把终点表示成「root 的第 N 个子节点之后」，容器是 root。
     只遍历子节点的话永远匹配不到 container → 返回 null → 选区被当成空的 →
     框选菜单不弹（作者框最后一段就没反应）。 */
  visit(root);
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
    /* 锚点段：`value` 里标记占着位置，DOM 里没有。
       进去之前加开始标记的长度，出来之后加结束标记的长度。 */
    const anchor = node instanceof HTMLElement ? node.dataset.anchor : undefined;
    if (anchor) acc += `⟦e:${anchor}⟧`.length;

    if (node.nodeType === Node.TEXT_NODE) {
      /* 缓冲区：偏移上占 0，只在 offset 正好落在它这里时作为落点。
         作者打字时光标停在缓冲区里，重绘后要放回同一处。 */
      if (node.parentElement && isBuffer(node.parentElement)) {
        if (acc >= offset) {
          point = {node, offset: 0};
          return true;
        }
        return false;
      }
      const len = serializeText(node.textContent ?? '').length;
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
    let hit = false;
    for (const child of Array.from(node.childNodes)) {
      if (visit(child)) {
        hit = true;
        break;
      }
    }
    if (anchor && !hit) acc += '⟦/e⟧'.length;
    return hit;
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
   * 槽位 key → 展示名。用来给正文里已框选的默认描述段加 hover 气泡：
   * 作者把鼠标停在一段高亮文字上，就知道「这是哪个槽位的默认描述」，
   * 不用回左边逐个展开槽位卡片去找。只影响 title，不改正文内容。
   */
  slotNames?: Record<string, string>;
  /**
   * 用户改动。返回 `false` = 这次写入**被拒**（父组件对引用有校验），
   * 编辑器会把 DOM 对齐回 `value`（见 `syncFromDom`）。
   */
  onChange: (text: string) => void | boolean;
  /** 程序写入（`@` 候选选中 / 粘贴）后的回调；返回值同上。 */
  onInsert: (text: string) => void | boolean;
  /**
   * 选区变化。父组件用它弹「设为槽位默认描述」的菜单。
   *
   * **只报有内容的选择**（`start !== end`）—— 光标停着不算选区，
   * 不然菜单会一直挂在光标旁边不走。
   *
   * 偏移是**带锚点标记**的正文坐标（与 `value` 同一坐标系），
   * 父组件可以直接拿去切正文。
   */
  onSelectionChange?: (
    range: {start: number; end: number} | null,
    point?: CaretPoint | null,
  ) => void;
  placeholder?: string;
  className?: string;
  id?: string;
  'aria-label'?: string;
  panelHint?: string;
  /**
   * 是否允许 @ 引用（弹候选面板）。
   *
   * 使用者改写「未选时文案」时**不需要**：他改的是一段话，不是往提示词里插引用。
   * `candidates` 仍会传进来 —— 它只是借来显示胶囊上的**槽位名**。
   */
  allowMention?: boolean;
}

export const RichPromptEditor = forwardRef<MentionTextareaHandle, RichPromptEditorProps>(
  function RichPromptEditor(
    { value, candidates, slotNames, onChange, onInsert, onSelectionChange, placeholder, className, id, 'aria-label': ariaLabel, panelHint, allowMention = true },
    ref,
  ) {
    const rootRef = useRef<HTMLDivElement>(null);
    const [mention, setMention] = useState<MentionQuery | null>(null);
    /* 菜单锚在**打字的那一刻**的光标处，之后不跟着每个字符跑。
       setMention 的同一个时刻读一次，键盘选中下一项时光标没动、菜单也不抖。 */
    const [caretPoint, setCaretPoint] = useState<CaretPoint | null>(null);
    /* 光标是否落在**默认描述段**内：段内只列可被引用的槽位（必填 / 已固定），
       段外是全部槽位。算出来而不是存一份过滤结果 —— 候选列表变了，
       存下来的那份会过期。 */
    const [inAnchor, setInAnchor] = useState(false);
    const usableCandidates = useMemo(
      () => (inAnchor ? candidates.filter(candidate => candidate.alwaysMaterial) : candidates),
      [candidates, inAnchor],
    );
    const updateMention = (next: MentionQuery | null) => {
      /* 使用者侧（改写兜底文案）：不弹 @ 面板。`candidates` 只是借来显示
         胶囊上的槽位名 —— 给使用者一个插引用的入口，只会引出「引用了可选槽位」
         那类问题（预检要求补兜底文案、编号也随位置变）。 */
      if (!allowMention) {
        setMention(null);
        setCaretPoint(null);
        return;
      }
      /* 默认描述段里可以引用**必定有素材**的槽位（必填 / 已固定）——
         那种槽位每次编译都有编号，引用成立。所以段内不弹空候选，而是
         只列可被引用的那些（`alwaysMaterial`）。

         判断光标是否落在高亮段内，直接看 DOM —— 用文本偏移算会被锚点标记和
         零宽缓冲区的换算绕进去，误伤正文其它位置的 @（用户报过「@ 全失效」）。 */
      let allowed = next;
      if (allowed) {
        const selection = window.getSelection();
        const node = selection?.anchorNode;
        const el = node instanceof HTMLElement ? node : node?.parentElement;
        const anchor = Boolean(el?.closest('[data-anchor]'));
        setInAnchor(anchor);
        if (anchor && !candidates.some(candidate => candidate.alwaysMaterial)) {
          allowed = null;
        }
      }
      setCaretPoint(allowed ? caretPointOf(rootRef.current) : null);
      setMention(allowed);
    };
    const [active, setActive] = useState(0);
    /** 组字中：这期间不重渲染 DOM，否则拼音会被吞掉 */
    const composing = useRef(false);
    /** 重建 DOM 之后要把光标放回的文本偏移 */
    const pendingCaret = useRef<number | null>(null);
    /** 外部「插入 token」要插到光标处，父组件拿不到，这里记着 */
    const caretRef = useRef(0);
    /*
     * **自己的撤销栈**。
     *
     * 浏览器的原生撤销在这里是不工作的：胶囊靠 `paint()` 重建 DOM，
     * 一重建就把元素的 undo 记录清了；粘贴又被我们 `preventDefault` + 改
     * `value` 接管，浏览器根本没记下那一步。于是 `Ctrl+Z` 什么也不做
     * （更糟的情况是把 DOM 改成和 `value` 不一致的样子）。
     *
     * 所以自己记：每接受一次改动就压一份 `{文本, 光标}`，`Ctrl+Z` 退一份、
     * `Ctrl+Shift+Z` / `Ctrl+Y` 进一份。**栈里只存文本**，不存 DOM ——
     * 文本才是真相，DOM 是按它重画出来的。
     */
    const history = useRef<{text: string; caret: number}[]>([{text: value, caret: 0}]);
    const historyAt = useRef(0);
    /** 这次改动是「打一个字」还是「一次整体动作」（粘贴 / 插 token / 撤销） */
    const origin = useRef<'type' | 'other'>('other');
    const lastPushAt = useRef(0);

    const matches = useMemo(
      () => (mention ? filterMentionChips(usableCandidates, mention.query) : []),
      [mention, usableCandidates],
    );
    const open = mention !== null && matches.length > 0;

    useEffect(() => {
      setActive(0);
    }, [matches.length, mention?.query]);

    /*
     * 选区上报：父组件据此弹「设为槽位默认描述」的菜单。
     *
     * **用 `selectionchange` 而不是 `mouseup`**：作者可能用键盘
     * （Shift+方向键）选一段，那条路没有 `mouseup`。
     *
     * **只报有内容的选择**：光标停着（`start === end`）不算 ——
     * 不然菜单会一直挂在光标旁边不走。
     *
     * **失焦时报 null**：点菜单本身会让编辑框失焦，那一刻要把菜单收起来，
     * 等下一次选区变化再弹（菜单按钮的 `onMouseDown` 阻止默认，
     * 所以焦点其实不会丢，见菜单那侧的处理）。
     */
    useEffect(() => {
      if (!onSelectionChange) return;
      const report = () => {
        const root = rootRef.current;
        if (!root) return;
        const range = selectionOffsets(root);
        if (!range || range.start === range.end) {
          onSelectionChange(null, null);
          return;
        }
        /* 第二个参数是选区右下角的视口坐标：框选菜单要浮在框的那段字旁边。 */
        onSelectionChange(range, selectionEndPoint());
      };
      document.addEventListener('selectionchange', report);
      return () => document.removeEventListener('selectionchange', report);
    }, [onSelectionChange]);

    /** 胶囊上显示哪个槽位名。
     *
     * 正文里通常是**作者写法**（`{{Mixed 1}}`），但也可能是内部写法
     * （`{{slot:hero}}`）—— 删过槽位的旧草稿里就有（那是 `stripSlotRefs`
     * 留下的形态）。两种都要认得，否则胶囊上显示的是一串原始 token。
     */
    const chipOf = (token: string) => {
      const norm = normalizeToken(token);
      const direct = candidates.find(candidate => normalizeToken(candidate.token) === norm);
      if (direct) return direct;
      /* 内部写法（`{{slot:hero}}`）也要认得：删过槽位的旧草稿里就有这种形态，
         认不出的话胶囊上显示的是一串原始 token，而不是槽位名。 */
      const key = norm.match(/^\{\{slot:([a-z][a-z0-9_]{0,39})/)?.[1];
      const label = key ? slotNames?.[key] : undefined;
      return label ? {token, label, all: false} : undefined;
    };

    /* 重建 DOM：text 节点 + 胶囊 + 高亮段交替。React 不管这些子节点。
     *
     * **两类特殊内容**：
     * - `{{Mixed N}}` 之类的引用 token → 胶囊（`contenteditable=false`）；
     * - `⟦e:key⟧…⟦/e⟧` 包住的默认描述 → **高亮的普通文本**。
     *
     * 高亮段为什么不是胶囊：它**要能被作者二次编辑**（改几个字）。
     * 胶囊是 `contenteditable=false` 的整体，退格一次整枚消失 ——
     * 而这段文字是作者要改的正文。`contenteditable` 的 span 里放普通文本，
     * 作者可以直接进去打字。
     */
    const paint = (root: HTMLElement, text: string) => {
      root.textContent = '';
      /* 先切出「锚点段」：它内部的 token 仍按普通文本处理
         （作者写的 `{{Mixed 1}}` 在默认描述里就是纯文字，
         编译器那边它也不该被当成引用 —— `empty_text` 不许含引用）。 */
      const anchors = parseAnchors(text);
      const segments: {text: string; anchorKey?: string}[] = [];
      let cursor = 0;
      for (const span of anchors) {
        /* `AnchorSpan.start/end` 是**内容**范围，标记本身在它外面。
           只按内容切会把 `⟦e:key⟧` / `⟦/e⟧` 当普通字画出来 ——
           作者打开一份已存过的草稿，正文里就明晃晃挂着两串内部标记。 */
        const openAt = text.lastIndexOf(`⟦e:${span.slotKey}⟧`, span.start);
        const closeAt = span.end + '⟦/e⟧'.length;
        if (openAt > cursor) segments.push({text: text.slice(cursor, openAt)});
        segments.push({text: text.slice(span.start, span.end), anchorKey: span.slotKey});
        cursor = closeAt;
      }
      if (cursor < text.length) segments.push({text: text.slice(cursor)});

      for (const segment of segments) {
        if (segment.anchorKey) {
          /* 前后各垫一枚零宽缓冲区：高亮段是首段/末段时，浏览器找不到段外的
             文本节点放光标，会把字打进高亮里（默认描述凭空多出几个字）。
             缓冲区给光标一个段外落点，序列化时零宽字符被剥掉。 */
          root.appendChild(caretBuffer());
          const span = highlight(segment.anchorKey);
          /* 段内的引用**也画成胶囊**：它确实是引用（2026-11-26 起，未选时文案
             可以引用必定有素材的槽位），画成裸的 `{{Mixed 1}}` 既难看，
             又让人以为那不是引用。胶囊自带底色与边框（一眼看出是引用），
             段自带下划线（一眼看出是默认描述）—— 两个特征叠在一起。 */
          let inner = 0;
          for (const match of segment.text.matchAll(TOKEN_RE)) {
            const at = match.index ?? 0;
            if (at > inner) {
              span.appendChild(document.createTextNode(segment.text.slice(inner, at)));
            }
            span.appendChild(capsule(match[0], chipOf(match[0]), true));
            inner = at + match[0].length;
          }
          if (inner < segment.text.length) {
            span.appendChild(document.createTextNode(segment.text.slice(inner)));
          }
          root.appendChild(span);
          root.appendChild(caretBuffer());
          continue;
        }
        let last = 0;
        for (const match of segment.text.matchAll(TOKEN_RE)) {
          const at = match.index ?? 0;
          if (at > last) root.appendChild(document.createTextNode(segment.text.slice(last, at)));
          root.appendChild(capsule(match[0], chipOf(match[0])));
          last = at + match[0].length;
        }
        if (last < segment.text.length) {
          root.appendChild(document.createTextNode(segment.text.slice(last)));
        }
      }
    };

    /** 光标缓冲区：零宽字符不可见，但浏览器会把它当文本节点放光标。 */
    const caretBuffer = () => {
      const span = document.createElement('span');
      span.dataset.caretBuffer = '';
      span.textContent = CARET_BUFFER;
      return span;
    };

    /**
     * 默认描述段：**带下划线的可编辑文字**，暖褐（quote）色系。
     *
     * 必须与「插槽引用胶囊」一眼可分：胶囊是**墨蓝描边的整体 token**
     * （contenteditable=false、有边框、不可逐字改）；这里是**作者写的字**
     * （软底 + 下划线、无边框、可直接编辑）。两者曾都用 primary 色 + 圆角边框，
     * 作者反馈「看着像一类东西」—— 但它们语义相反：一个是占位、一个是描述。
     */
    const highlight = (slotKey: string) => {
      const span = document.createElement('span');
      span.dataset.anchor = slotKey;
      span.className = cn(
        'bg-quote/8 px-0.5 font-medium text-quote',
        'underline decoration-quote/60 decoration-2 underline-offset-[3px]',
      );
      return span;
    };

    const capsule = (token: string, candidate?: MentionCandidate, inAnchor = false) => {
      const span = document.createElement('span');
      /* 光标不能进胶囊：`contenteditable=false` 让浏览器把它当一个整体，
         退格一次删掉整枚，而不是从名字中间逐字删。 */
      span.setAttribute('contenteditable', 'false');
      span.dataset.token = token;
      span.className = cn(
        'mx-0.5 inline-flex translate-y-[1px] cursor-default items-center rounded-xs border px-1.5 py-px align-baseline text-[0.95em] font-medium',
        candidate?.all
          ? 'border-quote/30 bg-quote/10 text-quote'
          : 'border-primary-line bg-primary-soft text-primary-deep',
        /* 在默认描述段里：胶囊自己的底色与边框（一眼看出是引用）
           + 继承段的下划线（一眼看出是默认描述）—— 两个特征同时在。 */
        inAnchor
          ? 'underline decoration-quote/60 decoration-2 underline-offset-[3px]'
          : '',
      );
      span.textContent = candidate?.label ?? token;
      return span;
    };

    /* 记录撤销点：`value` 变了就压一份。**按文本去重** —— 撤销时把指针退回去，
       `value` 恰好等于栈里那一份，这里就什么都不做（不必再区分「是不是撤销引起的」）。 */
    useEffect(() => {
      const current = history.current[historyAt.current];
      if (current && current.text === value) return;

      const at = Date.now();
      /* 连续打字合并成一步：一个词一次 `Ctrl+Z` 就够，不用一个字一个字退。
         判据里带上「指针在末尾」—— 撤销之后接着打，不能把新内容并进历史。 */
      const typingBurst =
        origin.current === 'type' &&
        at - lastPushAt.current < 800 &&
        historyAt.current === history.current.length - 1 &&
        historyAt.current > 0;
      lastPushAt.current = at;

      if (typingBurst) {
        history.current[historyAt.current] = {text: value, caret: caretRef.current};
        return;
      }

      const entries = history.current.slice(0, historyAt.current + 1);
      entries.push({text: value, caret: caretRef.current});
      // 上限：长提示词里逐字改会攒出几千份，留最近 100 份足够。
      if (entries.length > 100) entries.shift();
      history.current = entries;
      historyAt.current = entries.length - 1;
    }, [value]);

    /* 同步：值变了、或者 DOM 里还有没胶囊化的 token（用户手打出来的）才重建。 */
    useEffect(() => {
      const root = rootRef.current;
      if (!root || composing.current) return;

      const painted = root.querySelectorAll('[data-token]').length;
      /* expected 数**全部** token：锚点段（默认描述）里的引用现在也画成胶囊
         （段内的下划线 + 胶囊的边框同时在，两个特征一眼可分），所以段内段外
         都得数。两边必须一致才不会每次都重绘 —— 重绘会打断正在进行的鼠标
         拖选（选区塌缩 → 框选菜单不弹）。

         曾经段内引用画成纯文字、这里也就只数段外；改成段内也画胶囊后，
         计数没跟着改曾导致「默认描述里含插槽引用时那段框不出来」。 */
      const expected = expectedCapsuleCount(value);
      if (serialize(root) === value && painted === expected) return;

      const caret = pendingCaret.current;
      pendingCaret.current = null;
      paint(root, value);
      caretRef.current = caret ?? Math.min(caretRef.current, value.length);
      setCaret(root, caret ?? value.length);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [value, candidates]);

    /* 给默认描述高亮段加 hover 气泡。直接改 DOM 的 title，不重跑 paint ——
       重跑会把光标甩掉，作者可能正停在那段字上编辑。槽位改名时这个 effect
       会重跑，气泡跟着更新。 */
    useEffect(() => {
      const root = rootRef.current;
      if (!root) return;
      root.querySelectorAll<HTMLElement>('[data-anchor]').forEach((span) => {
        const key = span.dataset.anchor ?? '';
        const name = slotNames?.[key]?.trim();
        span.title = name
          ? `「${name}」的默认描述 · 使用者没选素材时用这段`
          : '某个槽位的默认描述';
      });
    }, [value, slotNames]);

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
      origin.current = 'other';
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

    const close = () => updateMention(null);

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
      origin.current = 'type';
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
        updateMention(detectMention(value, back));
        return;
      }
      // 提及面板由光标位置推导：删掉 `@` 它就自己收起
      updateMention(detectMention(text, at));
    };

    /** 退（-1）/ 进（+1）一份历史。返回 false = 没得退/进，或父组件拒了。 */
    const stepHistory = (delta: number): boolean => {
      const next = historyAt.current + delta;
      const target = history.current[next];
      if (!target) return false;

      origin.current = 'other';
      historyAt.current = next;
      const caret = Math.min(target.caret, target.text.length);
      pendingCaret.current = caret;
      caretRef.current = caret;
      if (onChange(target.text) === false) {
        /* 历史里的每一份都是被接受过的文本，正常走不到这儿；
           真被拒了就把指针退回去，别让栈和界面对不上。 */
        historyAt.current -= delta;
        pendingCaret.current = null;
        return false;
      }
      return true;
    };

    const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
      /* 撤销 / 重做要在最前面：它跟候选面板无关，面板关着也得能用。 */
      const mod = event.metaKey || event.ctrlKey;
      if (mod && (event.key === 'z' || event.key === 'Z')) {
        event.preventDefault();
        stepHistory(event.shiftKey ? 1 : -1);
        return;
      }
      if (mod && (event.key === 'y' || event.key === 'Y')) {
        event.preventDefault();
        stepHistory(1);
        return;
      }

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

    return (
      <div>
        <div className="relative z-20">
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
              'w-full overflow-y-auto whitespace-pre-wrap break-words rounded-lg border border-line bg-panel px-2.5 py-2 text-xs leading-[1.7] text-ink outline-none transition-colors',
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
            <span className="pointer-events-none absolute inset-x-2.5 top-2 text-xs leading-[1.7] text-ink-mute">
              {placeholder}
            </span>
          )}
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
      </div>
    );
  },
);
