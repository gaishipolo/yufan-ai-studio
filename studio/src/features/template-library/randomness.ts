/**
 * 模板库右栏的「随机性」三档。
 *
 * | 档 | 可选槽位 | 提示词里这一段怎么说 |
 * |---|---|---|
 * | **随机** | 不显示、不传素材 | 作者在封装页给可选槽位配的**多条随机文案**，前端抽一条注入 |
 * | **均衡**（默认） | **不显示** | 可选槽位用「未选时文案」（让给模型） |
 * | **自定义** | 显示 | 使用者挑素材，或用「未选时文案」 |
 *
 * **必填槽位在三档下都显示、都要使用者自己填** —— 必填意味着「不给素材
 * 就生成不了」，任何一档把它藏起来都等于让人卡死在界面上。三档的差别
 * **只在可选槽位**。
 *
 * ## 「随机」与「均衡」的差别只在文案来源
 *
 * 两档的可选槽位都**不显示、不传素材**（使用者没有可挑的）—— 差别是：
 * 均衡用作者写的那一段「未选时文案」（每次都一样），随机从作者配的
 * **多条随机文案**里抽一段（每次可能不同，场景描述不固定，视频才不千篇一律）。
 *
 * 随机由**前端**抽：compile 是纯函数（同一入参同一输出），所以前端把抽中的
 * 文案下标作为入参传给后端，后端只按选择展开，不自己掷骰子。
 *
 * ## 判据只有一处：`minCount === 0` 就是可选
 *
 * 与封装页的开关、后端 `compile.py` 的 `slot.min_count == 0` 同一口径。
 */
import type {SpecSlotOut} from '@/shared/api/schema';
import {balancedPinnedKeys} from './slotIndexRefs';
import {seededRandom} from './randomPick';

export type RandomnessMode = 'random' | 'balanced' | 'custom';

/** 三档的默认档位：均衡。 */
export const DEFAULT_RANDOMNESS: RandomnessMode = 'balanced';

/**
 * 三档的选项名。就叫「随机 / 均衡 / 自定义」—— 这三个词是这套功能自己的
 * 名字，界面外的地方（文档、沟通、需求单）都这么叫，改了会对不上。
 */
export const RANDOMNESS_LABEL: Record<RandomnessMode, string> = {
  random: '随机',
  balanced: '均衡',
  custom: '自定义',
};

/** 槽位是不是可选的（使用者可以不填）。判据只有 `minCount === 0`。 */
export function isOptionalSlot(slot: SpecSlotOut): boolean {
  return (slot.minCount ?? 1) === 0;
}

/**
 * 这一档下哪些槽位该出现在右栏。
 *
 * 随机 / 均衡 → 只列必填槽位（可选的不出现，前者走随机文案、后者走未选时文案）；
 * 自定义 → 列全部（必填的必须给人填，可选的让人挑素材或用未选时文案）。
 * 固定槽位三档都不出现 —— 作者选死的，填表人不能换也用不着知道。
 *
 * @param prompt 模板的提示词。均衡/随机档下有些可选槽位**不能**被让给模型
 *   （提示词里按「第 N 张」引用了它），那些要照常显示。见 `slotIndexRefs`。
 */
export function visibleSlots(
  slots: SpecSlotOut[],
  mode: RandomnessMode,
  prompt = '',
): SpecSlotOut[] {
  const user = slots.filter(slot => slot.binding !== 'fixed');
  if (mode === 'custom') return user;
  const pinned = balancedPinnedKeys(prompt, user);
  return user.filter(slot => !isOptionalSlot(slot) || pinned.includes(slot.key));
}

/**
 * 这一档下不该传素材的槽位。
 *
 * 随机 / 均衡 = 可选槽位一律不传（连同它们在界面上的消失）；
 * 自定义 = 不抑制任何槽位。
 *
 * @param prompt 提示词。被「第 N 张」引用钉住的可选槽位不抑制 —— 抑制了编译期就 400。
 */
export function suppressedSlotKeys(
  slots: SpecSlotOut[],
  mode: RandomnessMode,
  prompt = '',
): string[] {
  if (mode === 'custom') return [];
  const pinned = balancedPinnedKeys(prompt, slots);
  return slots
    .filter(slot => isOptionalSlot(slot) && !pinned.includes(slot.key))
    .map(slot => slot.key);
}

/**
 * 「随机」档：为每个**有随机文案的可选槽位**抽一段，返回 `{槽位key: 文案下标}`。
 *
 * - 只有随机档才调用；其它档不传 `randomTextChoices`，后端走既有逻辑。
 * - 被提示词「第 N 张」钉住的槽位不抽（它要传素材，不走文案）；与
 *   `suppressedSlotKeys` 同一判据，两边必须一致，否则一个抽了文案一个传了素材。
 * - 用 `seed` 造可复现随机源：渲染路径上不能有裸 `Math.random`，否则每次
 *   重渲染都重新抽，用户看到文案跳变。种子不变结果不变，种子变才是「再抽一次」。
 * - 只有一段文案的槽位也返回 `0`：后端会取那唯一一段，语义就是「用它」。
 */
export function pickRandomTextChoices(
  slots: SpecSlotOut[],
  prompt: string,
  seed: number,
): Record<string, number> {
  const rand = seededRandom(seed);
  const pinned = balancedPinnedKeys(prompt, slots);
  const choices: Record<string, number> = {};
  for (const slot of slots) {
    if (!isOptionalSlot(slot) || pinned.includes(slot.key)) continue;
    const texts = (slot.randomTexts ?? []).filter(text => text.trim() !== '');
    if (texts.length === 0) continue;
    // 用同一个 rand 顺序取，保证种子固定时整组结果可复现。
    choices[slot.key] = Math.min(texts.length - 1, Math.floor(rand() * texts.length));
  }
  return choices;
}

/** 手动挑的素材：`undefined` = 使用者没动过（随机/均衡档下由系统决定）。 */
export type ManualSlotValues = Record<string, string[] | undefined>;

/**
 * 真正提交给后端的素材 = 手动挑的，剔除这一档不该传的。
 *
 * 随机档不再有「系统挑素材」（素材随机已换成文案随机），所以这里只剩
 * 手动值 + 抑制名单两份；空槽位不占键，后端把「键存在但空数组」与
 * 「键不存在」当成两回事时不会踩坑。
 */
export function mergeSlotValues(
  manual: ManualSlotValues,
  suppressed: string[],
): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  for (const [key, ids] of Object.entries(manual)) {
    if (ids === undefined || suppressed.includes(key)) continue;
    if (ids.length) out[key] = ids;
  }
  return out;
}

/** 界面上某槽位显示哪些素材：只有手动值（随机档不填素材、可选槽位不显示）。 */
export function displaySlotValues(
  key: string,
  manual: ManualSlotValues,
): string[] {
  return manual[key] ?? [];
}
