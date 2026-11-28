/**
 * 封装页的 prompt 文档操作：改动入口 + 预检。
 *
 * 以前 `SpecWorkspace` 在四个事件处理器里各拼一套「长度 → 引用 → 可选槽位 →
 * 必填提示」的检查，发布预检又单独算一次。这里把它们收成一个模块，页面只负责
 * 把结果写进 state / toast。
 */
import type {TemplateSpecIn} from '@/shared/api/schema';
import {FORM_LIMITS} from '@/shared/lib/formLimits';
import {
  assignDefaultText as assignAnchorDefaultText,
  defaultTextRefError,
  optionalSlotRefCountError,
  optionalSlotRefError,
  parseAnchors,
  readDefaultText,
  syncDefaultTexts,
  unwrapAnchor,
} from './defaultText';
import {requiredSlotRefError} from './requiredRefs';
import {promptRefError, slotsMissingTag, toMixedTokens} from './specDraft';

export interface PromptEdit {
  ok: boolean;
  /** 成功时的下一份草稿；失败时为空。 */
  draft?: TemplateSpecIn;
  /** 给作者看的提示（错误或非阻塞提醒）。 */
  hint?: string;
  /** 成功后的 toast 文案。 */
  toast?: string;
}

/** 主提示词：拦非法引用；可选/必填问题只提示、不拦写入。 */
export function editMainPrompt(draft: TemplateSpecIn, text: string): PromptEdit {
  const slots = draft.slots ?? [];
  if (text.length > FORM_LIMITS.specPrompt) {
    return {
      ok: false,
      hint: `提示词不能超过 ${FORM_LIMITS.specPrompt} 字（存草稿的上限）`,
    };
  }

  const refError = promptRefError(text, slots);
  if (refError) return {ok: false, hint: refError};

  for (const span of parseAnchors(text)) {
    const error = defaultTextRefError(
      text.slice(span.start, span.end),
      slots,
      span.slotKey,
    );
    if (error) {
      return {ok: false, hint: `默认描述里${error}；请在框选段之外引用`};
    }
  }

  const hint =
    optionalSlotRefError(text, slots) ??
    requiredSlotRefError(text, slots) ??
    '';
  const authored = toMixedTokens(text, slots);
  const nextSlots = syncDefaultTexts(authored, slots, draft.prompt ?? '');
  return {ok: true, draft: {...draft, prompt: authored, slots: nextSlots}, hint};
}

/** 随机档提示词：与主提示词同一套引用规则，但可选槽位最多出现一次。 */
export function editRandomPrompt(draft: TemplateSpecIn, text: string): PromptEdit {
  const slots = draft.slots ?? [];
  if (text.length > FORM_LIMITS.specPrompt) {
    return {
      ok: false,
      hint: `随机档提示词不能超过 ${FORM_LIMITS.specPrompt} 字（存草稿的上限）`,
    };
  }
  const error =
    promptRefError(text, slots) ?? optionalSlotRefCountError(text, slots);
  if (error) return {ok: false, hint: error};
  return {
    ok: true,
    draft: {...draft, randomPrompt: toMixedTokens(text, slots)},
    hint: '',
  };
}

/** 把正文里选中的一段设为某槽位的默认描述。 */
export function assignDefaultText(
  draft: TemplateSpecIn,
  slotKey: string,
  range: {start: number; end: number},
): PromptEdit {
  const slots = draft.slots ?? [];
  const authored = draft.prompt ?? '';
  const next = assignAnchorDefaultText(authored, slotKey, range);
  const picked = readDefaultText(next, slotKey);
  const pickedError = defaultTextRefError(picked, slots, slotKey);
  if (pickedError) {
    return {ok: false, hint: `默认描述里${pickedError}；请重新框一段纯文字`};
  }

  const nextSlots = syncDefaultTexts(next, slots, authored);
  const slot = nextSlots.find(item => item.key === slotKey);
  const name = slot?.label?.trim() || '该槽位';
  return {
    ok: true,
    draft: {...draft, prompt: next, slots: nextSlots},
    hint: requiredSlotRefError(next, nextSlots) ?? '',
    toast: `「${name}」的默认描述已设为「${slot?.emptyText?.trim() ?? ''}」`,
  };
}

/** 取消一段默认描述：摘锚点、文字留在正文。 */
export function cancelDefaultText(draft: TemplateSpecIn, slotKey: string): PromptEdit {
  const slots = draft.slots ?? [];
  const authored = draft.prompt ?? '';
  const next = unwrapAnchor(authored, slotKey);
  if (next === authored) return {ok: true};
  const nextSlots = syncDefaultTexts(next, slots, authored);
  return {
    ok: true,
    draft: {...draft, prompt: next, slots: nextSlots},
    hint: requiredSlotRefError(next, nextSlots) ?? '',
    toast: '已取消默认描述，这段文字仍是普通正文',
  };
}

/** 发布前的本地预检：只做后端会拒、但前端能提前点名的检查。 */
export function publishPreflight(spec: TemplateSpecIn): string | null {
  const missing = slotsMissingTag(spec.slots ?? []);
  if (!missing.length) return null;
  return (
    `这些槽位还没圈候选标签：${missing.join('、')}。` +
    '没圈的话列不出可用的候选：素材槽位退化成全部素材，包槽位干脆列不出包。'
  );
}