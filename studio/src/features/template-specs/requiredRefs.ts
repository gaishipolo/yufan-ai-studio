/**
 * 「必填槽位必须在正文里被引用」的前端判据。
 *
 * 与后端 `core/template_spec/rules.py::validate_required_slot_refs` **同一口径**
 * （2026-11-27 收紧）：必填槽位（使用者自己挑的那种）的引用必须落在**正文**里
 * —— 框选的默认文本里写了不算数。
 *
 * ## 为什么框选段里写了不算
 *
 * 框选的默认描述段在提交时会变成那个槽位的「未选时文案」：
 *
 * - 使用者给那一格挑了素材 → 它被「选中时文案」顶掉；
 * - 使用者（自定义档）或模型（随机档）改写那一格 → 它被换掉。
 *
 * 同一格只能二选一（挑了素材就不会用改写后的文案）→ 那处引用随时会消失。
 * 所以正文里必须再提一次 —— 那才是永远在的那份。
 *
 * ## 「正文」怎么算
 *
 * 编辑态里，框选段还带着锚点标记（⟦e:key⟧…⟦/e⟧）与它包住的文字；提交时那段
 * 整体变成槽位引用、文字挪进 `emptyText`。所以后端看到的「正文」
 * = 编辑态的提示词**去掉框选段的内容**。这里算的就是它。
 *
 * 两个写点要一起改：后端 `rules.py::validate_required_slot_refs` 与本文件。
 */
import type {SpecSlotIn} from '@/shared/api/schema';
import {bodyText} from './defaultText';
import {referencedKeys} from './specDraft';

/**
 * 必填槽位在正文里被引用了吗？
 *
 * 返回 `null` = 没问题；否则是给作者看的理由。
 *
 * **只提示、不拦写入**：作者可能正在把引用挪到正文，半路上的草稿本来就不合法；
 * 拦写入会让人改不动（每敲一个字都被弹回来）。
 */
export function requiredSlotRefError(
  prompt: string,
  slots: SpecSlotIn[],
): string | null {
  const required = slots.filter(
    slot =>
      (slot.refMode ?? 'mixed') === 'mixed' &&
      (slot.minCount ?? 1) >= 1 &&
      (slot.binding ?? 'user') !== 'fixed',
  );
  if (!required.length) return null;
  const bodyRefs = new Set(referencedKeys(bodyText(prompt), slots));
  for (const slot of required) {
    if (bodyRefs.has(slot.key)) continue;
    const name = slot.label?.trim() || slot.key;
    return (
      `槽位「${name}」是必填的，但正文里没有引用它 —— ` +
      '框选的默认描述里写了不算（那段话会被顶掉、也会被改写丢弃）；请在正文里引用它'
    );
  }
  return null;
}
