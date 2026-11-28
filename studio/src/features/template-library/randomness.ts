/**
 * 模板库右栏的「随机性」三档。
 *
 * | 档 | 可选槽位 | 兜底文案由谁写 |
 * |---|---|---|
 * | **随机** | 不显示、不传素材 | 模型改写每个可选槽位的「未选时文案」 |
 * | **均衡**（默认） | **不显示** | 作者写好的「未选时文案」 |
 * | **自定义** | 显示 | 使用者挑素材，或自己改写那一格文案 |
 *
 * **必填槽位在三档下都显示、都要使用者自己填** —— 必填意味着「不给素材
 * 就生成不了」，任何一档把它藏起来都等于让人卡死在界面上。三档的差别
 * **只在可选槽位**。
 *
 * ## 随机档改的是「文案」，不是「素材」
 *
 * 随机 / 均衡两档都不显示、不传可选素材 —— 差别只在兜底文案是谁写的：
 * 均衡用作者原文，随机由后端调模型改写同一段原文（每次点生成重写一次）。
 * 所以「随机」不会换人物 / 服装 / 场景的素材，只换描述它们的说法。
 *
 * 模板没有「可改写的可选槽位」时这一档不可用 —— 判据见
 * `rewritableSlotKeys`（与后端 `randomness.rewritable_slot_keys` 同五条）。
 *
 * ## 判据只有一处：`minCount === 0` 就是可选
 *
 * 与封装页的开关、后端 `compile.py` 的 `slot.min_count == 0` 同一口径。
 */
import type {SpecSlotOut, SpecVersionOut} from '@/shared/api/schema';
import {balancedPinnedKeys, slotRefModes} from './slotIndexRefs';

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

/**
 * 三档各自「用这一档生成，出来会是什么样」的一句话，显示在控件下面。
 *
 * 与上面的 `RANDOMNESS_LABEL` 分开记：档名是这套功能自己的名字（不能改），
 * 2026-11-28 用户要求改口径：随机档改的是「默认描述文案」，不是素材本身。
 * 旧文案「随机的人物、服装、场景」会让人以为素材随机，必须避免。
 *
 * 也**不是** `Segmented` 的 `hints`（那句挂在 `title` 上，讲的是机制）。
 * 这句讲的是结果，三句都在 28 字以内，400px 的右栏一行放得下。
 */
export const RANDOMNESS_DESCRIPTION: Record<RandomnessMode, string> = {
  random: '可选槽位没挑素材时，模型改写默认描述文案',
  balanced: '每次生成和演示案例相似的内容',
  custom: '通过参考图和提示词精准控制人物、服装、场景等元素',
};

/** 槽位是不是可选的（使用者可以不填）。判据只有 `minCount === 0`。 */
export function isOptionalSlot(slot: SpecSlotOut): boolean {
  return (slot.minCount ?? 1) === 0;
}

/**
 * 可选槽位的**填法**：从资产库挑素材，还是直接改写提示词里那一格。
 *
 * 与「随机性三档」是两个轴：档位决定「可选槽位由谁给」，填法决定
 * 「给了的话是素材还是文字」。只有自定义档下的可选槽位才有得选填法。
 */
export type SlotFillMode = 'pick' | 'text';

/**
 * 这一档下哪些槽位该出现在右栏。
 *
 * 随机 / 均衡 → 只列必填槽位（可选的不出现，前者走随机文案、后者走未选时文案）；
 * 自定义 → 列全部（必填的必须给人填，可选的让人挑素材或用未选时文案）。
 * 固定槽位三档都不出现 —— 作者选死的，填表人不能换也用不着知道。
 *
 * @param prompt 模板的提示词。均衡/随机档下有些可选槽位**不能**被让给模型
 *   （被单张引用却没写未选时文案），那些要照常显示。见 `slotIndexRefs`。
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
 * @param prompt 提示词。被单张引用且没写未选时文案的可选槽位不抑制 ——
 * 抑制了编译期就 400。
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

/** 手动挑的素材：`undefined` = 使用者没动过（随机/均衡档下由系统决定）。 */
export type ManualSlotValues = Record<string, string[] | undefined>;

type RandomnessVersion = Pick<SpecVersionOut, 'prompt' | 'slots'>;

/**
 * 这份已发布版本里**能被模型改写**的可选槽位。
 *
 * 五条与后端 `randomness.rewritable_slot_keys` 同源：
 * 1. `refMode === 'mixed'`（只有图 / 成片参与槽位引用）；
 * 2. `minCount === 0`（可选）；
 * 3. 不是 `binding=fixed`；
 * 4. 提示词里有单张插入点 `{{slot:key}}`；
 * 5. `emptyText` 非空（模型要有原文才能「语义不变、措辞不同」地改写）。
 *
 * 只用**已发布版本**：草稿里改了没发版，使用者拿到的仍是旧版本。
 */
export function rewritableSlotKeys(
  version: RandomnessVersion | null | undefined,
): string[] {
  if (!version) return [];
  const slots = version.slots ?? [];
  const modes = slotRefModes(version.prompt ?? '', slots);
  return slots
    .filter(slot => {
      if ((slot.refMode ?? 'mixed') !== 'mixed') return false;
      if (!isOptionalSlot(slot)) return false;
      if ((slot.binding ?? 'user') === 'fixed') return false;
      if (!modes[slot.key]?.has('')) return false;
      return Boolean((slot.emptyText ?? '').trim());
    })
    .map(slot => slot.key);
}

/** 这个模板能不能选「随机」档：至少要有一个可改写槽位。 */
export function hasRewritableSlots(
  version: RandomnessVersion | null | undefined,
): boolean {
  return rewritableSlotKeys(version).length > 0;
}

/**
 * 使用者选的档位 → **实际生效**的档位。
 *
 * 唯一的分支：选了随机档、但这份模板没有可改写槽位 → 按默认档走。
 *
 * 为什么用派生而不是在 effect 里改 state：换模板时 state 可能还停在 `random`，
 * 而新模板没有可改写槽位。effect 同步中间有一个渲染窗口，那期间界面显示
 * 「随机」、提交也会带上那个标记，后端 400；派生让两者永远一致。
 */
export function resolveRandomness(
  chosen: RandomnessMode,
  version: RandomnessVersion | null | undefined,
): RandomnessMode {
  return chosen === 'random' && !hasRewritableSlots(version)
    ? DEFAULT_RANDOMNESS
    : chosen;
}

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
