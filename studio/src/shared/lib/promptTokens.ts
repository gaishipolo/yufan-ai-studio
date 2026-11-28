/**
 * 提示词引用语法：**唯一**的 token 形状与表达式解析。
 *
 * 后端 `core/template_spec/rules.py` / `compile.py` 认三种写法：
 * `{{slot:key}}`、`{{Mixed N}}`、`{{Text N}}`；槽位引用允许 `*` / `.N`
 * 修饰符（虽然新模板已不写）。前端五个文件曾各抄一份正则，形状漂移过一次：
 * 带空格的引用被当成没引用，改槽位 key 后保存才 400。
 *
 * 这里只放**语法**，不放业务判断（哪些槽位可引用、编号怎么算）。
 */

export const SLOT_KEY_SOURCE = '[a-z][a-z0-9_]{0,39}';
const MODIFIER = String.raw`(?:\*|\.\d+)`;

/** `{{slot:key}}` / `{{slot:key*}}` / `{{slot:key.2}}`，两边允许空格。 */
export function slotRefPattern(key?: string): RegExp {
  const name = key ? key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') : SLOT_KEY_SOURCE;
  return new RegExp(`\\{\\{\\s*slot:(${name})(${MODIFIER})?\\s*\\}\\}`, 'g');
}

/** 只认单张引用 `{{slot:key}}`（不带 `*` / `.N`）。 */
export function singleSlotRefPattern(key?: string): RegExp {
  const name = key ? key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') : SLOT_KEY_SOURCE;
  return new RegExp(`\\{\\{\\s*slot:(${name})\\s*\\}\\}`, 'g');
}

/** `{{Mixed N}}` / `{{Mixed N*}}` / `{{Mixed N.2}}`。 */
export function mixedRefPattern(): RegExp {
  return new RegExp(`\\{\\{\\s*Mixed\\s+(\\d+)(${MODIFIER})?\\s*\\}\\}`, 'g');
}

/** 正文里的 Mixed / slot 引用（胶囊与输入框的 token）。 */
export function tokenPattern(): RegExp {
  return new RegExp(
    `\\{\\{\\s*(?:Mixed\\s+\\d+|slot:${SLOT_KEY_SOURCE})${MODIFIER}?\\s*\\}\\}`,
    'g',
  );
}

/** 渲染用：任何 `{{...}}` 都算 token，认不出的交给 parse 返回 null。 */
export function anyTokenPattern(): RegExp {
  return /\{\{\s*([^{}]+?)\s*\}\}/g;
}

export type PromptExpression =
  | {kind: 'slot'; key: string; modifier: string; at?: number}
  | {kind: 'mixed'; index: number; modifier: string; at?: number}
  | {kind: 'text'; index: number};

/** 解析 `{{...}}` 里的表达式（不含花括号），认不出返回 null。 */
export function parsePromptExpression(raw: string): PromptExpression | null {
  const expression = raw.trim();
  const slot = new RegExp(
    `^slot:(${SLOT_KEY_SOURCE})(${MODIFIER})?$`,
  ).exec(expression);
  if (slot) {
    const modifier = slot[2] ?? '';
    const at = modifier.startsWith('.') ? Number(modifier.slice(1)) : undefined;
    return {kind: 'slot', key: slot[1], modifier, at};
  }
  const mixed = new RegExp(`^Mixed\\s+(\\d+)(${MODIFIER})?$`).exec(expression);
  if (mixed) {
    const modifier = mixed[2] ?? '';
    const at = modifier.startsWith('.') ? Number(modifier.slice(1)) : undefined;
    return {kind: 'mixed', index: Number(mixed[1]), modifier, at};
  }
  const text = /^Text\s+(\d+)$/.exec(expression);
  if (text) return {kind: 'text', index: Number(text[1])};
  return null;
}
