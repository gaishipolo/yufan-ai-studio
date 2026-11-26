import type { CaretPoint } from './CursorMenu';

/**
 * 取光标的**视口坐标**，给浮在打字处的菜单当锚点。
 *
 * 两种输入框走两条路：
 * - `contenteditable`（提示词正文）：浏览器自己给选区矩形，直接读；
 * - `textarea`（槽位卡片的选中时文案）：没有原生 API，用一份**镜像 div**
 *   复刻样式与光标前的文字，量末尾那个 span 的位置。
 *
 * 都返回视口坐标（clientX/clientY 那一套）—— 菜单用 fixed 定位，
 * 不掺进页面的滚动偏移。
 */

const MIRROR_STYLE_PROPS = [
  'boxSizing',
  'width',
  'paddingTop',
  'paddingRight',
  'paddingBottom',
  'paddingLeft',
  'borderTopWidth',
  'borderRightWidth',
  'borderBottomWidth',
  'borderLeftWidth',
  'fontFamily',
  'fontSize',
  'fontWeight',
  'fontStyle',
  'letterSpacing',
  'lineHeight',
  'textTransform',
  'textIndent',
  'textAlign',
  'whiteSpace',
  'wordWrap',
  'wordBreak',
  'overflowWrap',
  'tabSize',
] as const;

/** contenteditable：选区（折叠时就是光标）的矩形。 */
function fromSelection(): CaretPoint | null {
  const selection = window.getSelection();
  /* 没有选区时给合成锚点：真实浏览器里打字时光标一定有选区；
     jsdom 下 user.type 不建立 DOM 选区，测试靠这个锚点仍能验证菜单行为。 */
  if (!selection || selection.rangeCount === 0) return { x: 0, y: 0 };
  const range = selection.getRangeAt(0);
  /* jsdom 两种 rect 方法都没实现 —— 没有真实布局可量。给一个合成锚点，
     让菜单照常渲染（测试关心的是键盘/选中行为，不是像素位置）。 */
  if (typeof range.getClientRects !== 'function' && typeof range.getBoundingClientRect !== 'function') {
    return { x: 0, y: 0 };
  }
  const rects = typeof range.getClientRects === 'function' ? range.getClientRects() : null;
  const rect =
    rects && rects.length > 0
      ? rects[rects.length - 1]
      : typeof range.getBoundingClientRect === 'function'
        ? range.getBoundingClientRect()
        : null;
  if (!rect) return { x: 0, y: 0 };
  /* 全是 0 = jsdom 没有布局（真实浏览器里光标矩形不会宽高都 0）。 */
  if (rect.left === 0 && rect.top === 0 && rect.width === 0 && rect.height === 0) {
    return { x: 0, y: 0 };
  }
  return { x: rect.left, y: rect.bottom };
}

/** textarea：镜像 div 量光标。 */
function fromTextarea(node: HTMLTextAreaElement): CaretPoint | null {
  const mirror = document.createElement('div');
  const style = window.getComputedStyle(node);
  for (const prop of MIRROR_STYLE_PROPS) {
    mirror.style[prop] = style[prop];
  }
  mirror.style.position = 'absolute';
  mirror.style.visibility = 'hidden';
  mirror.style.overflow = 'hidden';
  mirror.style.height = 'auto';
  mirror.style.whiteSpace = 'pre-wrap';
  mirror.style.wordWrap = 'break-word';

  const before = node.value.slice(0, node.selectionStart ?? 0);
  mirror.textContent = before;
  const marker = document.createElement('span');
  /* 放一个字符，量它左上角 —— 空 span 没有尺寸量不到。 */
  marker.textContent = '\u200b';
  mirror.appendChild(marker);
  document.body.appendChild(mirror);
  try {
    const rect = marker.getBoundingClientRect();
    const owner = node.getBoundingClientRect();
    return {
      x: owner.left + (rect.left - mirror.getBoundingClientRect().left) - node.scrollLeft,
      y: owner.top + (rect.top - mirror.getBoundingClientRect().top) - node.scrollTop,
    };
  } finally {
    mirror.remove();
  }
}

export function caretPointOf(node: HTMLElement | null): CaretPoint | null {
  if (!node) return null;
  if (node instanceof HTMLTextAreaElement) return fromTextarea(node);
  return fromSelection();
}

/** 一段选区（不是光标）的右下角，给框选菜单当锚点。 */
export function selectionEndPoint(): CaretPoint | null {
  return fromSelection();
}
