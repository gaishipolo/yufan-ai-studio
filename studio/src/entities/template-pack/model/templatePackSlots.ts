import type { TemplatePackSlotIn } from '@/shared/api/schema';

/**
 * 槽位编辑态。
 *
 * 与后端 `TemplatePackSlotIn` 同形，但多几个**只在编辑期有意义**的字段：
 * `error` 是就地报错位（不弹 toast），`assetName` 让作者知道自己钉的是哪张图。
 */
export interface TemplatePackSlotDraft {
  token: string;
  slotKind: 'fixed' | 'filtered';
  label: string;
  assetId: string;
  assetIdSpace: 'v1' | 'v2';
  mediaType: string;
  filterKind: string;
  filterTagIds: string[];
  required: boolean;
  error?: string;
}

/** 第 1 槽固定是参考视频，其余是用户能换的输入。 */
export const SOURCE_SLOT_TOKEN = '{{Mixed 1}}';

/** 槽位序号 → 占位符原文。编号从 1 起连续。 */
export function tokenOf(position: number): string {
  return `{{Mixed ${position}}}`;
}

/** 占位符原文 → 序号。认不出来的返回 0（调用方当「不合法」处理）。 */
export function positionOf(token: string): number {
  const matched = /^\{\{Mixed ([1-9]\d*)\}\}$/.exec(token.trim());
  return matched ? Number(matched[1]) : 0;
}

/**
 * 提示词里引用到的槽位序号，按出现顺序去重。
 *
 * 与后端 `cited_tokens` 同一口径 —— 前端据此提示「提示词里的
 * `{{Mixed 5}}` 没有对应槽位」，但**最终判定在后端**。
 */
export function citedPositions(prompt: string): number[] {
  const seen: number[] = [];
  const pattern = /\{\{Mixed ([1-9]\d*)\}\}/g;
  let matched = pattern.exec(prompt);
  while (matched) {
    const number = Number(matched[1]);
    if (!seen.includes(number)) seen.push(number);
    matched = pattern.exec(prompt);
  }
  return seen;
}

/** 新建一条空草稿时的槽位列表：第 1 槽是参考视频。 */
export function initialSlots(): TemplatePackSlotDraft[] {
  return [
    {
      token: SOURCE_SLOT_TOKEN,
      slotKind: 'fixed',
      label: '参考视频',
      assetId: '',
      assetIdSpace: 'v2',
      mediaType: 'video',
      filterKind: '',
      filterTagIds: [],
      required: true,
    },
    {
      token: tokenOf(2),
      slotKind: 'filtered',
      label: '',
      assetId: '',
      assetIdSpace: 'v2',
      mediaType: 'image',
      filterKind: 'image',
      filterTagIds: [],
      required: true,
    },
  ];
}

/** 编辑态 → 入参。发给后端前统一成契约形状。 */
export function toSlotIn(draft: TemplatePackSlotDraft): TemplatePackSlotIn {
  const base = {
    token: draft.token,
    slotKind: draft.slotKind,
    label: draft.label,
    required: draft.required,
  };
  if (draft.slotKind === 'fixed') {
    return {
      ...base,
      assetId: draft.assetId,
      assetIdSpace: draft.assetIdSpace,
      mediaType: draft.mediaType as TemplatePackSlotIn['mediaType'],
    };
  }
  return {
    ...base,
    filterKind: draft.filterKind as TemplatePackSlotIn['filterKind'],
    filterTagIds: draft.filterTagIds,
    filterTagMatch: 'any',
  };
}

/**
 * 编辑态的本地校验。
 *
 * **这是给作者看的即时反馈，不是最终判定** —— 后端会再判一遍，
 * 且后端的判定才是契约。这里只拦明显不成立的东西，让作者在保存前
 * 就知道「还差什么」。
 */
export function validateSlots(
  slots: TemplatePackSlotDraft[],
  prompt: string,
): Record<string, string> {
  const errors: Record<string, string> = {};

  if (!slots.length) {
    errors['_form'] = '至少要有一个槽位';
    return errors;
  }

  slots.forEach((slot, index) => {
    const expected = tokenOf(index + 1);
    if (slot.token !== expected) {
      errors[slot.token] = `槽位编号必须连续，这一槽应该是 ${expected}`;
    }
    if (slot.slotKind === 'fixed') {
      if (!slot.assetId) {
        errors[slot.token] = '固定素材槽位要选一条素材';
      }
    } else if (!slot.filterKind) {
      errors[slot.token] = '筛选槽位要指定素材类别';
    }
  });

  const first = slots[0];
  if (first && (first.slotKind !== 'fixed' || first.mediaType !== 'video')) {
    errors[first.token] = '第一个槽位必须是参考视频';
  }

  const available = new Set(slots.map(slot => positionOf(slot.token)));
  const missing = citedPositions(prompt).filter(number => !available.has(number));
  if (missing.length) {
    errors['_prompt'] = `提示词引用了没有对应槽位的编号：${missing
      .map(number => `{{Mixed ${number}}}`)
      .join('、')}`;
  }

  return errors;
}
