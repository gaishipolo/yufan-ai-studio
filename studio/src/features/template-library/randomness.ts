/**
 * 模板库右栏的「随机性」三档。
 *
 * | 档 | 可选槽位 | 提示词用哪一段 |
 * |---|---|---|
 * | **随机** | 不显示、不传素材 | 作者在封装页另写的**随机档提示词**（整段替换） |
 * | **均衡**（默认） | **不显示** | 主提示词，可选槽位用「未选时文案」（让给模型） |
 * | **自定义** | 显示 | 主提示词，使用者自己挑素材 |
 *
 * **必填槽位在三档下都显示、都要使用者自己填** —— 必填意味着「不给素材
 * 就生成不了」，任何一档把它藏起来都等于让人卡死在界面上。三档的差别
 * **只在可选槽位**。
 *
 * ## 「随机」与「均衡」的差别只在用哪段提示词
 *
 * 两档的可选槽位都**不显示、不传素材**（使用者没有可挑的）—— 差别是：
 * 均衡用主提示词，随机整段换成作者写的随机档提示词（场景描述完全不同，
 * 视频才不千篇一律）。
 *
 * 切换发生在后端：前端只传 `useRandomPrompt`。**选哪一段**是使用者的决定，
 * 但两段都是**确定的文字**，所以 `compile` 仍然是纯函数（同一入参同一输出）。
 *
 * 模板没配随机档提示词时这一档不可选 —— 判据见 `hasRandomPrompt`。
 *
 * ## 判据只有一处：`minCount === 0` 就是可选
 *
 * 与封装页的开关、后端 `compile.py` 的 `slot.min_count == 0` 同一口径。
 */
import type {SpecSlotOut, SpecVersionOut} from '@/shared/api/schema';
import {balancedPinnedKeys} from './slotIndexRefs';

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

/** 手动挑的素材：`undefined` = 使用者没动过（随机/均衡档下由系统决定）。 */
export type ManualSlotValues = Record<string, string[] | undefined>;

/**
 * 这个模板能不能选「随机」档。
 *
 * 只看**已发布版本**有没有那一段：草稿里写了但没发版，使用者拿到的仍是旧
 * 版本，选随机档会被后端 400。前端据此把档位置灰并说明原因 —— 让用户点得到
 * 一个必然失败的档，比不给这个档更糟。
 */
export function hasRandomPrompt(
  version: Pick<SpecVersionOut, 'randomPrompt'> | null | undefined,
): boolean {
  return Boolean(version?.randomPrompt?.trim());
}

/**
 * 使用者选的档位 → **实际生效**的档位。
 *
 * 唯一的分支：选了随机档、但这个模板没配那一段 → 按默认档走。
 *
 * 为什么用派生而不是在 effect 里改 state：换模板时 state 可能还停在 `random`，
 * 而新模板没配。effect 同步中间有一个渲染窗口，那期间界面显示「随机」、
 * 提交也会带上那个标记，后端 400；派生让两者永远一致。
 */
export function resolveRandomness(
  chosen: RandomnessMode,
  version: Pick<SpecVersionOut, 'randomPrompt'> | null | undefined,
): RandomnessMode {
  return chosen === 'random' && !hasRandomPrompt(version) ? DEFAULT_RANDOMNESS : chosen;
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
