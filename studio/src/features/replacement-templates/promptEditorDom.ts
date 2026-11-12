export interface PromptRange { start: number; end: number }

export const PROMPT_TOKEN_PATTERN = /\{\{(?:Mixed \d+|ScenePrompt|ReplacementScope)\}\}/g;

/** 只读正文和原始引用标记，标签内的缩略图/名称永不参与序列化。 */
export function readPromptDom(root: Node): string {
  if (root.nodeType === Node.TEXT_NODE) return root.textContent ?? '';
  if (root instanceof HTMLElement && root.dataset.promptToken) return root.dataset.promptToken;
  if (root.nodeName === 'BR') return '\n';
  if (root.childNodes.length === 1 && root.firstChild?.nodeName === 'BR') return '';
  let text = '';
  root.childNodes.forEach((child, index) => {
    if (/^(DIV|P)$/.test(child.nodeName) && index > 0) text += '\n';
    text += readPromptDom(child);
  });
  return text;
}

export function getPromptSelection(root: HTMLElement): PromptRange | null {
  const selection = window.getSelection();
  if (!selection?.rangeCount) return null;
  const range = selection.getRangeAt(0);
  if (!root.contains(range.startContainer) || !root.contains(range.endContainer)) return null;
  const offset = (node: Node, position: number) => {
    const prefix = document.createRange();
    prefix.selectNodeContents(root);
    prefix.setEnd(node, position);
    return readPromptDom(prefix.cloneContents()).length;
  };
  return {start: offset(range.startContainer, range.startOffset), end: offset(range.endContainer, range.endOffset)};
}

export function setPromptSelection(root: HTMLElement, range: PromptRange) {
  function point(position: number): [Node, number] {
    let remaining = Math.max(0, position);
    let found: [Node, number] | undefined;
    function visit(parent: Node) {
      for (const [index, child] of [...parent.childNodes].entries()) {
        const length = readPromptDom(child).length;
        if (remaining > length) {remaining -= length; continue;}
        if (child.nodeType === Node.TEXT_NODE) {found = [child, remaining]; return;}
        if (child instanceof HTMLElement && child.dataset.promptToken) {
          found = [parent, index + (remaining === 0 ? 0 : 1)]; return;
        }
        visit(child);
        if (found) return;
        found = [parent, index + 1]; return;
      }
    }
    visit(root);
    return found ?? [root, root.childNodes.length];
  }
  const result = document.createRange();
  result.setStart(...point(range.start));
  result.setEnd(...point(range.end));
  const selection = window.getSelection();
  selection?.removeAllRanges();
  selection?.addRange(result);
}

export function atomicPromptRange(text: string, range: PromptRange, direction: 'backward' | 'forward'): PromptRange {
  let {start, end} = range;
  if (start === end) {
    const segments = [...new Intl.Segmenter(undefined, {granularity: 'grapheme'}).segment(text)];
    if (direction === 'backward') start = segments.filter(segment => segment.index < start).at(-1)?.index ?? 0;
    else {
      const next = segments.find(segment => segment.index >= end);
      end = next ? next.index + next.segment.length : text.length;
    }
  }
  for (const match of text.matchAll(PROMPT_TOKEN_PATTERN)) {
    const left = match.index, right = left + match[0].length;
    if (start < right && end > left) {start = Math.min(start, left); end = Math.max(end, right);}
  }
  return {start, end};
}
