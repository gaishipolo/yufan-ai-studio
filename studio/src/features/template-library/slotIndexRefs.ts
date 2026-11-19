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
 * 后端 `core/template_spec/compile.py::_expander` 里，三种引用写法的
 * 「没填素材」语义完全不同：
 *
 * | 写法 | 没填素材时 | 均衡档能省掉吗 |
 * |---|---|---|
 * | 根本没引用 | 无影响 | **能** |
 * | `{{slot:x*}}`（整组） | 降级为空 | **能** |
 * | `{{slot:x}}`（单张） | 用「未选时文案」兜底；**没写兜底就报错** | 写了兜底**能**，没写**不能** |
 * | `{{slot:x.2}}`（第 N 张） | **必然报错**（没有「第 2 张」这回事） | **不能** |
 *
 * 所以「能省掉」的条件是：**没被引用**、或**只被整组引用**、或
 * **被单张引用且作者写了「未选时文案」**。
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
 * 返回 `{槽位key: Set<写法>}`，写法为 `""`（单张）、`"*"`（整组）、
 * `".2"`（第 N 张）。
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
 * 提示词里「第 N 张」引用到的**最大 N**。
 *
 * `{{slot:x.3}}` 意味着这个槽位至少要填 **3** 张 —— 后端展开时取
 * `owned[index - 1]`，填了 1 张就报「第 3 张不存在（这次填了 1 张）」。
 *
 * 这个下限**与档位无关**：随机 / 均衡 / 自定义，只要提示词里写了
 * 「第 3 张」，这个槽位就至少得 3 张素材。
 */
export function indexedRefCounts(
  prompt: string,
  slots: SpecSlotOut[] = [],
): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const [key, used] of Object.entries(slotRefModes(prompt, slots))) {
    for (const mode of used) {
      if (!mode.startsWith('.')) continue;
      const index = Number(mode.slice(1));
      if (!Number.isFinite(index)) continue;
      counts[key] = Math.max(counts[key] ?? 0, index);
    }
  }
  return counts;
}

/**
 * 这个槽位**实际**最少要填几张。
 *
 * 平时就是作者写的 `minCount`。但提示词里的引用写法会**加码**：
 *
 * | 提示词写法 | 实际下限 | 依据 |
 * |---|---|---|
 * | 没被引用 | `minCount` | — |
 * | `{{slot:x*}}`（整组） | `minCount` | 没填时降级为空，不报错 |
 * | `{{slot:x}}`（单张） | `max(minCount, 1)` | 没填且无兜底文案 → 报错 |
 * | `{{slot:x.N}}`（第 N 张） | `max(minCount, N)` | 取 `owned[N-1]`，越界就报错 |
 *
 * ## 为什么这层要单独算
 *
 * `minCount` 是「作者希望使用者最少给几张」，是**意图**；
 * 提示词里的引用是**硬约束**（后端 `compile.py::_expander`：
 * 单张引用走 `_fallback_or_raise`，没兜底就报错；`.N` 取 `owned[index-1]`，
 * 越界就报错）。
 * 拿 `minCount` 当唯一判据的后果是：界面显示「不缺料」，用户一点生成，
 * 后端回一句「第 3 张不存在」—— 使用者完全看不出是自己的模板有问题。
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

  /* 「第 N 张」：实际要 N 张。 */
  const indexed = indexedRefCounts(prompt, slots)[slot.key] ?? 0;
  if (indexed > 0) return Math.max(base, indexed);

  /* 单张引用且没有兜底文案：不填就报错 → 至少 1 张。
     整组引用没填时降级为空，不需要兜。 */
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
    /* 「第 N 张」必然报错 —— 一定要传。 */
    if ([...used].some(mode => mode.startsWith('.'))) {
      pinned.push(slot.key);
      continue;
    }
    /* 单张引用：作者写了「未选时文案」才能省掉，没写就必须传。 */
    if (used.has('') && !(slot.emptyText ?? '').trim()) {
      pinned.push(slot.key);
    }
  }
  return pinned;
}
