import type {
  SpecPromptIn,
  SpecSlotIn,
  TemplateSpecIn,
  TemplateSpecOut,
} from '@/shared/api/schema';

/**
 * 封装编辑器的草稿模型：**直接复用契约类型**。
 *
 * 刻意不另造一套本地模型：槽位字段与契约一一对应，多一层映射就多一处漂移
 * （"页面能过、后端 400"）。这里只提供"从哪来、怎么改"的小工具，不做规则校验
 * —— 规则在 `core.template_spec`（服务端），前端复制一份就会走岔。
 */

export type SlotType = SpecSlotIn['type'];
export type SlotSource = NonNullable<SpecSlotIn['source']>;
export type SlotBinding = NonNullable<SpecSlotIn['binding']>;

export const SLOT_TYPE_LABELS: Record<SlotType, string> = {
  image: '图片',
  video: '视频',
  audio: '音频',
  text: '文本',
};

export function newKey(prefix: string): string {
  return `${prefix}-${Math.random().toString(36).slice(2, 8)}`;
}

export function blankPrompt(): SpecPromptIn {
  return {locked: '', editable: ''};
}

export function blankSlot(position = 0): SpecSlotIn {
  return {
    key: `slot_${Math.random().toString(36).slice(2, 6)}`,
    type: 'image',
    source: 'asset',
    binding: 'user',
    refMode: 'mixed',
    minCount: 1,
    maxCount: 1,
    label: '',
    usage: '',
    position,
  };
}

export function blankSpec(): TemplateSpecIn {
  return {
    key: newKey('spec'),
    name: '',
    description: '',
    model: '',
    params: {},
    prompt: blankPrompt(),
    slots: [blankSlot(0)],
    attributes: {},
  };
}

/** 已保存/已发布的规格 → 可编辑草稿。优先用草稿（可能比发布版更新）。 */
export function fromSpecOut(out: TemplateSpecOut): TemplateSpecIn {
  if (out.draft) return {...out.draft, revision: out.revision};
  if (out.latest) {
    return {
      key: out.key,
      name: out.name,
      description: out.description,
      model: out.latest.model,
      params: out.latest.params ?? {},
      prompt: out.latest.prompt ?? blankPrompt(),
      slots: (out.latest.slots ?? []).map((slot, index) => ({...slot, position: index})),
      attributes: {},
      revision: out.revision,
    };
  }
  return {...blankSpec(), key: out.key, name: out.name, revision: out.revision};
}

/** 提交前把槽位按当前顺序重排 `position`：拖拽之后位置就是唯一真相。 */
export function normalize(spec: TemplateSpecIn): TemplateSpecIn {
  return {
    ...spec,
    slots: (spec.slots ?? []).map((slot, index) => ({...slot, position: index})),
  };
}

export interface RefChip {
  /** 插入提示词的文本，如 `{{slot:hero}}`。 */
  token: string;
  label: string;
  /** 整组引用：多张时一次写出全部编号。 */
  all: boolean;
}

/**
 * 可被提示词引用的槽位。
 *
 * `attachment`（音频/文本）不参与编号，所以不能引用 —— 服务端会 400。
 * 这里提前不显示，避免用户写出必然失败的引用。
 */
export function referenceChips(spec: TemplateSpecIn): RefChip[] {
  return (spec.slots ?? [])
    .filter(slot => (slot.refMode ?? 'mixed') === 'mixed')
    .sort((a, b) => (a.position ?? 0) - (b.position ?? 0))
    .flatMap(slot => {
      const name = slot.label?.trim() || slot.key;
      const chips: RefChip[] = [{token: `{{slot:${slot.key}}}`, label: name, all: false}];
      const max = slot.source === 'pack' ? 2 : (slot.maxCount ?? 1);
      if (max > 1 || slot.source === 'pack') {
        chips.push({token: `{{slot:${slot.key}*}}`, label: `${name}（全部）`, all: true});
      }
      return chips;
    });
}

/** 在光标处插入引用，返回新文本与新光标位置（受控 textarea 需要它来定位）。 */
export function insertRef(
  text: string,
  token: string,
  caret: number,
): {text: string; caret: number} {
  const at = Math.max(0, Math.min(caret, text.length));
  const before = text.slice(0, at);
  const after = text.slice(at);
  // 前后各补一个空格：中文里紧贴文字会让模型把 token 当成词语的一部分。
  const lead = before && !/\s$/.test(before) ? ' ' : '';
  const tail = after && !/^\s/.test(after) ? ' ' : '';
  const inserted = `${lead}${token}${tail}`;
  return {text: before + inserted + after, caret: at + inserted.length};
}

/** 提示词里引用了哪些槽位 —— 给编辑器提示"这条引用指向谁"。 */
export function referencedKeys(text: string): string[] {
  const hits = text.matchAll(/\{\{slot:([a-z][a-z0-9_]{0,39})(?:\*|\.\d+)?\}\}/g);
  return [...new Set([...hits].map(match => match[1]))];
}
