/**
 * 模板库右栏的「随机性」三档。
 *
 * | 档 | 可选槽位 | 素材怎么来 |
 * |---|---|---|
 * | **随机** | 显示 | 系统**随机挑**几张填进去，使用者可再改 |
 * | **均衡**（默认） | **不显示** | 可选槽位**一律不传素材**（让给模型） |
 * | **自定义** | 显示 | 使用者自己挑 |
 *
 * **必填槽位在三档下都显示、都要使用者自己填** —— 必填意味着「不给素材
 * 就生成不了」，任何一档把它藏起来都等于让人卡死在界面上。三档的差别
 * **只在可选槽位**。
 *
 * ## 三档是「谁决定可选素材」的三种分工
 *
 * - **随机**：使用者不挑，系统替他挑 —— 用途是「给我看看效果 / 试试手气」。
 * - **均衡**：使用者只挑**必须**的那几样，可选的让位给模型自己发挥 ——
 *   用途是「我只给关键素材，剩下的你决定」。
 * - **自定义**：使用者全权决定 —— 用途是「我每一张都要指定」。
 *
 * ## 为什么「均衡」= 不显示可选槽位 + 不传素材
 *
 * 这两件事必须**同时**做，只做一件会得到两种不同的错误：
 *
 * - 只不显示、不传 → 提示词里那个可选槽位的段落没了（作者写了「未选时文案」
 *   也不生效），使用者的意图被悄悄丢掉。
 * - 只显示、不传 → 界面上摆着一个填不了的槽位，用户不知道该干嘛。
 *
 * 所以「均衡」下可选槽位**既不出现在右栏，也不进 `slotValues`** ——
 * 两者是同一件事的两面。
 *
 * ## 判据只有一处：`minCount === 0` 就是可选
 *
 * 与封装页的开关、后端 `compile.py` 的 `slot.min_count == 0` 同一口径。
 * 别在别处再写一遍 —— 三处不同步时，界面显示「可选」而编译器按必填处理
 * （或反过来），而这种错只在生成结果里显形。
 */
import type {SpecSlotOut} from '@/shared/api/schema';
import {balancedPinnedKeys} from './slotIndexRefs';

export type RandomnessMode = 'random' | 'balanced' | 'custom';

/** 三档的默认档位：均衡。 */
export const DEFAULT_RANDOMNESS: RandomnessMode = 'balanced';

/**
 * 三档的**选项名**。
 *
 * 就叫「随机 / 均衡 / 自定义」—— 这三个词是这套功能自己的名字，
 * 界面外的地方（文档、口头沟通、需求单）都这么叫，改了会对不上。
 *
 * 名字不承担解释职责：每档在界面上**做什么**是看得见的
 * （随机档系统填好并标「系统」、均衡档可选槽位整个不出现、
 * 自定义档摆出可挑的格子），布局本身就把区别说清楚了。
 */
export const RANDOMNESS_LABEL: Record<RandomnessMode, string> = {
  random: '随机',
  balanced: '均衡',
  custom: '自定义',
};

/**
 * 槽位是不是**可选**的（使用者可以不填）。
 *
 * 判据只有 `minCount === 0`。
 */
export function isOptionalSlot(slot: SpecSlotOut): boolean {
  return (slot.minCount ?? 1) === 0;
}

/**
 * 这一档下**哪些槽位该出现在右栏**。
 *
 * - 均衡 → **只列必填槽位**（可选的整个让给模型，不出现）
 * - 随机 / 自定义 → 列全部（必填的必须给人填，可选的看这一档怎么处理）
 *
 * **固定槽位（`binding === 'fixed'`）在三种档位下都不出现** ——
 * 那是作者在封装页选死的，填表的人既不能换也用不着知道它长什么样。
 *
 * @param prompt 模板的提示词。均衡档下有些可选槽位**不能**被让给模型
 *   （提示词里按「第 N 张」引用了它），那些要照常显示。见 `slotIndexRefs`。
 */
export function visibleSlots(
  slots: SpecSlotOut[],
  mode: RandomnessMode,
  prompt = '',
): SpecSlotOut[] {
  const user = slots.filter(slot => slot.binding !== 'fixed');
  if (mode === 'balanced') {
    const pinned = balancedPinnedKeys(prompt, user);
    return user.filter(slot => !isOptionalSlot(slot) || pinned.includes(slot.key));
  }
  return user;
}

/**
 * 这一档下**不该传素材**的槽位。
 *
 * 「均衡」= 可选槽位一律不传（连同它们在界面上的消失）。
 * 「随机」「自定义」= 不传任何槽位（随机档由调用方负责自动填）。
 *
 * @param prompt 模板的提示词。均衡档下被「第 N 张」引用的槽位**不抑制** ——
 *   抑制了编译期就 400。见 `slotIndexRefs`。
 */
export function suppressedSlotKeys(
  slots: SpecSlotOut[],
  mode: RandomnessMode,
  prompt = '',
): string[] {
  if (mode === 'balanced') {
    const pinned = balancedPinnedKeys(prompt, slots);
    return slots
      .filter(slot => isOptionalSlot(slot) && !pinned.includes(slot.key))
      .map(slot => slot.key);
  }
  return [];
}

/**
 * 随机档：每个可选槽位自动挑几张。
 *
 * **为什么是 `maxCount` 而不是 `minCount`**：可选槽位的 `minCount` 是 0，
 * 按它挑就等于「一个都不挑」—— 那随机档就退化成均衡档了。既然是随机，
 * 就把该槽位能接受的数量**填满**，让每个可选槽位都真的参与进来。
 *
 * @param slots 当前模板的全部槽位（只取可选的）
 * @param pools 槽位 key → 候选素材 id 列表
 * @param pick 从候选池里挑的函数（可注入随机源以便测试）
 */
export function autoFilledSlots(
  slots: SpecSlotOut[],
  pools: Record<string, string[]>,
  pick: (pool: string[], count: number) => string[],
): Record<string, string[]> {
  const filled: Record<string, string[]> = {};
  for (const slot of slots.filter(isOptionalSlot)) {
    const pool = pools[slot.key] ?? [];
    if (pool.length === 0) continue;
    const want = Math.max(1, slot.maxCount ?? 1);
    filled[slot.key] = pick(pool, want);
  }
  return filled;
}

/**
 * 使用者手动挑的素材。
 *
 * **值是 `string[] | undefined`，`undefined` 有含义**：它表示「这个槽位
 * 使用者没动过」，与「挑了又删空」是两件事 —— 后者必须盖掉系统随机填的，
 * 前者必须让随机填的留下。用 `[]` 当「没动过」会把「我不要这张」也
 * 解释成「没动过」，随机档下就永远删不掉系统挑的那张。
 *
 * **这里只装使用者亲手挑的**，系统随机填的那几张住在另一处
 * （`autoFilled`）—— 两份分开，「切档时要不要继承」这件事就不存在了：
 * 切走时系统那份还在那儿，切回来时换种子重挑。
 */
export type ManualSlotValues = Record<string, string[] | undefined>;

/**
 * 这一档下**真正会提交**给后端的素材。
 *
 * 三份数据合起来：
 *
 * 1. `auto` —— 随机档系统挑的；
 * 2. `manual` —— 使用者手动挑的（**盖过** `auto`）；
 * 3. `suppressed` —— 这一档不该传的（均衡档的可选槽位），**从结果里剔除**。
 *
 * 空的槽位不占键：后端把「键存在但数组为空」与「键不存在」当成两回事
 * 的地方不少，统一按「没传」处理最省心。
 */
export function mergeSlotValues(
  manual: ManualSlotValues,
  auto: Record<string, string[]>,
  suppressed: string[],
): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  for (const [key, ids] of Object.entries(auto)) {
    if (suppressed.includes(key)) continue;
    out[key] = ids;
  }
  for (const [key, ids] of Object.entries(manual)) {
    if (ids === undefined) continue;
    if (suppressed.includes(key)) {
      delete out[key];
      continue;
    }
    out[key] = ids;
  }
  for (const key of Object.keys(out)) {
    if (!out[key].length) delete out[key];
  }
  return out;
}

/**
 * 界面上某个槽位该显示哪些素材。
 *
 * 手动挑过的显示手动挑的（哪怕挑空了）；没动过的显示系统随机填的
 * （随机档）。均衡档下可选槽位根本不显示，这个函数不会被问到。
 */
export function displaySlotValues(
  key: string,
  manual: ManualSlotValues,
  auto: Record<string, string[]>,
): string[] {
  const own = manual[key];
  if (own !== undefined) return own;
  return auto[key] ?? [];
}

/**
 * 切档时「随机 → 别的档」：把系统随机填的那几张**接管**成手挑的。
 *
 * ## 为什么要有这一步
 *
 * 界面上显示的是「手动优先、否则系统随机」。切到自定义档后系统随机
 * **不再参与提交**（`activeAuto` 只在随机档有值），如果不做接管，
 * 那几张就从界面上消失了 —— 使用者明明刚看到它们填在那里。
 *
 * ## 只接管「使用者没动过」的
 *
 * 使用者亲手挑过的那几格已经是手挑的值了，接管等于覆盖他的决定。
 * `undefined` 才是「没动过」（见 `ManualSlotValues`）。
 *
 * @returns 接管后的手挑值，以及哪些槽位是「接管来的」（供交还时用）
 */
export function adoptAutoValues(
  manual: ManualSlotValues,
  auto: Record<string, string[]>,
): {values: ManualSlotValues; carried: string[]} {
  const values: ManualSlotValues = {...manual};
  const carried: string[] = [];
  for (const [key, ids] of Object.entries(auto)) {
    if (values[key] !== undefined) continue;
    values[key] = ids;
    carried.push(key);
  }
  return {values, carried};
}

/**
 * 切档时「别的档 → 随机」：把**接管来的**那几格交还给系统。
 *
 * 不交还的后果：「随机 → 自定义 → 随机」绕一圈回来还是原来那几张，
 * 「随机」就成了摆设 —— 而使用者按「换一批」时看到的是新的。
 *
 * **使用者亲手挑过的不动**（不在 `carried` 里的）：那是他的决定，
 * 系统随机不该覆盖。
 */
export function releaseCarriedValues(
  manual: ManualSlotValues,
  carried: string[],
): ManualSlotValues {
  const values: ManualSlotValues = {...manual};
  for (const key of carried) delete values[key];
  return values;
}
