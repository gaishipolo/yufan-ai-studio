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

/** 域名尾巴的形状：点号后面两个以上字母（`com` / `cn` / `io`）。 */
const DOMAIN_TAIL = /^[A-Za-z]{2,}$/;

/**
 * 这一段是不是在写**邮箱地址**。
 *
 * **只看「像不像地址」，不看「@ 前面挨着什么」。**
 *
 * 原来的判据是「@ 前面是词字符就算地址」—— 那是把一个冷门case
 * （提示词正文里写邮箱）的防护，开在了常用路径上：提示词里写英文太常见了
 * （`cinematic handheld shot` 后面接一个 @ 想引槽位），
 * 而提示词里写邮箱地址几乎见不到。**一堵错的墙比一个冷门case 贵得多**，
 * 用户报的「输入一串英文再 @ 就失效」就是这么来的。
 *
 * 现在只有**真的打出了地址形状**才收起：
 * - `@` 后面已经跟出域名尾巴（`a@b.com`）；
 * - `@` 前面就是一个域名、后面还没写（`site.com@`，正在写收件方那一段）。
 */
function looksLikeAddress(text: string, at: number, caret: number): boolean {
  const tail = text.slice(at + 1, caret);

  // @ 后面已经打出了域名尾巴
  const tailDot = tail.lastIndexOf('.');
  if (tailDot >= 0 && DOMAIN_TAIL.test(tail.slice(tailDot + 1))) return true;

  // @ 前面就是一个域名（site.com@），后面还没写 —— 正在写收件方那一段
  if (!tail) {
    const before = text.slice(0, at);
    // @ 前面那个「词」：从最后一个空白往后
    const word = before.slice(before.search(/[^\s]*$/));
    const wordDot = word.lastIndexOf('.');
    if (wordDot > 0 && DOMAIN_TAIL.test(word.slice(wordDot + 1))) return true;
  }
  return false;
}

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
 * - 打出域名形状的是邮箱地址（见 `looksLikeAddress`）。
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
  if (looksLikeAddress(text, index, at)) return null;
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
