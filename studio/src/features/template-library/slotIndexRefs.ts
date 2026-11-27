import type {SpecSlotOut} from '@/shared/api/schema';
import {toSlotTokens} from '@/features/template-specs/specDraft';

/**
 * 「均衡」档下哪些槽位**不能**被让给模型。
 *
 * 「均衡」的语义是「可选槽位一律不传素材」。但有些槽位**不能不传** ——
 * 传了就 400，而且那句话（「第 2 张不存在」「引用了未填素材的槽位」）
 * 与「我用的是均衡模式」看不出关系，使用者只会以为模板坏了。
 *
 * ## 判据：提示词里怎么引用它
 *
 * 一槽一素材之后只剩**一种**引用写法（`{{slot:x}}`）：填了素材插「选中时
 * 文案」，没填插「未选时文案」，都没写就报错。
 * 「整组」（`{{slot:x*}}`）与「第 N 张」（`{{slot:x.2}}`）已经被结构校验拒
 * （后端 `rules.py::_validate_ref_modifier`），这里不再认它们。
 *
 * 所以「能省掉」的条件是：**没被引用**，或**被引用且作者写了「未选时文案」**。
 *
 * ## 与后端的形状必须一致
 *
 * 后端 `compile.py::_SLOT_REF` 的形状是
 * `\{\{\s*slot:([a-z][a-z0-9_]{0,39})(\*|\.\d{1,3})?\s*\}\}`，
 * **两边允许空格**。这里抄同一份 —— 形状漂移过一次的后果是
 * 「界面以为不冲突、后端 400」。
 */
const SLOT_REF = /\{\{\s*slot:([a-z][a-z0-9_]{0,39})(\*|\.\d{1,3})?\s*\}\}/g;

/**
 * 提示词里每个槽位被**哪种写法**引用了。
 *
 * 返回 `{槽位key: Set<写法>}`。新模板只有 `""`（单张）合法；`"*"` / `".2"`
 * 是历史写法，后端结构校验已直接拒，这里保留形状只为分析旧版本不漂移。
 */
export function slotRefModes(
  prompt: string,
  slots: SpecSlotOut[] = [],
): Record<string, Set<string>> {
  const modes: Record<string, Set<string>> = {};
  /* 作者写的是 `{{Mixed N}}`（按槽位顺序编号），这里按 key 解析 ——
     编号会随槽位增删而变，key 不会。 */
  for (const match of toSlotTokens(prompt, slots).matchAll(SLOT_REF)) {
    const key = match[1];
    const modifier = match[2] ?? '';
    if (!modes[key]) modes[key] = new Set<string>();
    modes[key].add(modifier);
  }
  return modes;
}

/**
 * 这个槽位在提示词里**有兜底文案的插入点**吗。
 *
 * 一槽一素材后只有单张引用 `{{slot:x}}` 是插入点；整组 / 第 N 张已被
 * 结构校验拒，不是另一种「没填时」的说法。
 *
 * 判据给「改提示词」用：没有插入点时，使用者敲的字一个也不会进提示词，
 * 那就**不该给他这个入口**（摆一个打了字也不生效的输入框，
 * 与已删的 `usage` 字段是同一类错）。
 */
export function hasFallbackSlotRef(
  prompt: string,
  key: string,
  slots: SpecSlotOut[] = [],
): boolean {
  const used = slotRefModes(prompt, slots)[key];
  return Boolean(used && used.has(''));
}

/**
 * 这个槽位**实际**最少要填几张。
 *
 * 平时就是作者写的 `minCount`。但提示词里的引用写法会**加码**：
 *
 * | 提示词写法 | 实际下限 | 依据 |
 * |---|---|---|
 * | 没被引用 | `minCount` | — |
 * | `{{slot:x}}`（单张） | `max(minCount, 1)` | 没填且无兜底文案 → 报错 |
 *
 * ## 为什么这层要单独算
 *
 * `minCount` 是「作者希望使用者最少给几张」，是**意图**；
 * 提示词里的引用是**硬约束**（后端 `compile.py::_expander`：
 * 单张引用走 `_fallback_or_raise`，没兜底就报错）。
 * 拿 `minCount` 当唯一判据的后果是：界面显示「不缺料」，用户一点生成，
 * 后端回一句「引用了未填素材的槽位」—— 使用者完全看不出是自己的模板有问题。
 *
 * **与档位无关**：这个下限来自提示词本身，三档下都一样。
 */
export function requiredSlotCount(
  slot: SpecSlotOut,
  prompt: string,
  slots: SpecSlotOut[] = [],
): number {
  const base = slot.minCount ?? 1;
  const used = slotRefModes(prompt, slots)[slot.key];
  if (!used) return base;

  /* 被引用了、而作者没写兜底文案 → 不填就报错 → 至少 1 张。 */
  if (used.has('') && !(slot.emptyText ?? '').trim()) {
    return Math.max(base, 1);
  }
  return base;
}

/**
 * 这些槽位在「均衡」档下**必须**传素材。
 *
 * @param prompt 模板的提示词
 * @param slots 当前模板的槽位（只关心可选的那些）
 */
export function balancedPinnedKeys(
  prompt: string,
  slots: SpecSlotOut[],
): string[] {
  const modes = slotRefModes(prompt, slots);
  const pinned: string[] = [];
  for (const slot of slots) {
    const used = modes[slot.key];
    if (!used) continue;
    /* 被单张引用：作者写了「未选时文案」才能省掉，没写就必须传。 */
    if (used.has('') && !(slot.emptyText ?? '').trim()) {
      pinned.push(slot.key);
    }
  }
  return pinned;
}
