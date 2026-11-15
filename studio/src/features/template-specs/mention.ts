/**
 * 提示词框里的 `@` 提及（mention）。
 *
 * **只做「在输入框里选槽位」这一件事**，不改契约：选中之后写进提示词的仍然是
 * `{{slot:key}}` —— 跟下方按钮插入的 token 一模一样，编译器完全看不出区别。
 *
 * 为什么需要它：以前插入引用只有「下方一排 chip」一个入口，作者得先低头找按钮、
 * 再抬头找光标位置，点完还得确认光标没跑。输入 `@` 是在**写提示词的自然动作里**
 * 完成的 —— 想到哪写到哪，槽位名直接冒出来选。
 */

/** mention 的查询词上限：超了基本是在写正文而不是在找槽位。 */
const MAX_QUERY = 24;

export interface MentionQuery {
  /** `@` 所在位置（替换时要从这里开始）。 */
  start: number;
  /** 查询词结束位置（= 光标）。 */
  end: number;
  /** `@` 与光标之间的文字，作为过滤词。 */
  query: string;
}

/**
 * 光标处是否正在输入 `@`。
 *
 * 判定要点（每条都对应一种"看起来像 @ 但不是引用"的正文）：
 * - 查询词里不能有空白与花括号 —— `@` 后跟空格就是正文里的 at someone；
 * - `@` 前一个字若是邮箱/域名常见的字符（`a@b.com`），那是地址不是提及。
 *
 * 用 `lastIndexOf` 找最近的 @：连打两个 @（`@图@像`）时后一个才是当前这次提及。
 */
export function detectMention(text: string, caret: number): MentionQuery | null {
  const at = Math.max(0, Math.min(caret, text.length));
  const before = text.slice(0, at);
  const index = before.lastIndexOf('@');
  if (index < 0) return null;
  const query = before.slice(index + 1);
  if (query.length > MAX_QUERY) return null;
  if (/[\s{}[\]]/.test(query)) return null;
  const prev = index > 0 ? before[index - 1] : '';
  if (prev && /[A-Za-z0-9._+-]/.test(prev)) return null;
  return {start: index, end: at, query};
}

/** 按查询词过滤候选：匹配展示名（用户看得到的那部分）。 */
export function filterMentionChips<T extends {label: string; token: string}>(
  chips: T[],
  query: string,
): T[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return chips;
  return chips.filter(
    chip =>
      chip.label.toLowerCase().includes(needle) || chip.token.toLowerCase().includes(needle),
  );
}

/**
 * 用 token 替换掉 `@查询词`，返回新文本与新光标。
 *
 * 前后各补一个空格：中文里 token 紧贴文字会让模型把它当成词语的一部分
 * （跟 `insertRef` 同一条理由）。
 */
export function applyMention(
  text: string,
  range: {start: number; end: number},
  token: string,
): {text: string; caret: number} {
  const head = text.slice(0, range.start);
  const tail = text.slice(range.end);
  const lead = head && !/\s$/.test(head) ? ' ' : '';
  const gap = tail && !/^\s/.test(tail) ? ' ' : '';
  const inserted = `${lead}${token}${gap}`;
  return {text: head + inserted + tail, caret: range.start + inserted.length};
}
