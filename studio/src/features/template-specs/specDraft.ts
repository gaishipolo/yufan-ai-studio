import type {
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

/** 槽位 key 的前缀：自动生成、界面上不显示，但提示词里的 `{{slot:image_1}}` 要人能读。 */
const SLOT_KEY_PREFIX: Record<SlotType, string> = {
  image: 'image',
  video: 'video',
  audio: 'audio',
  text: 'text',
};

/** 取一个同类型里最小可用的序号，避免 `image_1` 重复。 */
export function nextSlotKey(slots: SpecSlotIn[], type: SlotType): string {
  const used = new Set(slots.map(slot => slot.key));
  const prefix = SLOT_KEY_PREFIX[type];
  for (let index = 1; index <= 99; index += 1) {
    const candidate = `${prefix}_${index}`;
    if (!used.has(candidate)) return candidate;
  }
  return `${prefix}_${Math.random().toString(36).slice(2, 6)}`;
}

export function blankSlot(position = 0, type: SlotType = 'image', slots: SpecSlotIn[] = []): SpecSlotIn {
  return {
    key: nextSlotKey(slots, type),
    type,
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
    prompt: '',
    slots: [blankSlot(0, 'image', [])],
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
      prompt: out.latest.prompt ?? '',
      slots: (out.latest.slots ?? []).map((slot, index) => ({...slot, position: index})),
      attributes: {},
      revision: out.revision,
    };
  }
  return {...blankSpec(), key: out.key, name: out.name, revision: out.revision};
}

/**
 * 改槽位类型时顺带改名 —— **只在没被引用过时**。
 *
 * key 是按类型自动生成的（`image_1`），类型改成视频后还叫 `image_1` 会误导人；
 * 但提示词里可能已经有 `{{slot:image_1}}`，改名等于让那条引用静默失效（编译期
 * 报「引用了不存在的槽位」）。所以：用过就不动，没用过才跟着类型改名。
 */
export function rekeySlot(
  slots: SpecSlotIn[],
  index: number,
  nextType: SlotType,
  prompt: string,
): SpecSlotIn[] {
  const slot = slots[index];
  if (!slot || slot.type === nextType) return slots;
  const used = new RegExp(`\\{\\{slot:${slot.key}(?:\\*|\\.\\d+)?\\}\\}`).test(prompt);
  if (used) return slots;
  const next = slots.slice();
  next[index] = {
    ...slot,
    type: nextType,
    key: nextSlotKey(slots.filter((_, position) => position !== index), nextType),
  };
  return next;
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
 * `text` 槽位虽然是附加输入（不占 Mixed 编号），但有自己的一套编号：编译时
 * 按槽位顺序拆成 `{{Text N}}`，执行层再按 `textLibraryIds` 把正文注进去。
 * 这是模板里唯一"填表时可换内容"的地方，所以它必须能引用。
 * `audio` 没有编号空间，引用必然 400 —— 这里直接不显示。
 */
export function referenceChips(spec: TemplateSpecIn): RefChip[] {
  let ordinal = 0;
  return (spec.slots ?? [])
    .filter(slot => (slot.refMode ?? 'mixed') === 'mixed' || slot.type === 'text')
    .sort((a, b) => (a.position ?? 0) - (b.position ?? 0))
    .flatMap(slot => {
      ordinal += 1;
      // key 是自动生成且界面上不显示，所以回退名用「图片 1」这种位置可读名。
      const name = slot.label?.trim() || `${SLOT_TYPE_LABELS[slot.type]} ${ordinal}`;
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

/**
 * 删槽位时把它在提示词里的引用一起清掉。
 *
 * 留着就是一条必然失败的引用：编译期会报「引用了不存在的槽位」，而那句话出现在
 * 用户点生成的时候，看不出跟"刚才删了个槽位"有关。所以删的时候一并清，并回一个
 * 条数让调用方告诉用户清了几处 —— 不要静默改用户的提示词。
 */
export function stripSlotRefs(
  text: string,
  key: string,
): {text: string; count: number} {
  const pattern = new RegExp(
    `\\{\\{\\s*slot:${key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?:\\*|\\.\\d+)?\\s*\\}\\}`,
    'g',
  );
  const count = (text.match(pattern) ?? []).length;
  if (count === 0) return {text, count};
  // 引用两边常各带一个空格（插入时补的），删掉后会把两处空格并成一处。
  return {text: text.replace(pattern, '').replace(/[ \t]{2,}/g, ' '), count};
}

/** 提示词里引用了哪些槽位 —— 给编辑器提示"这条引用指向谁"。 */
export function referencedKeys(text: string): string[] {
  const hits = text.matchAll(/\{\{slot:([a-z][a-z0-9_]{0,39})(?:\*|\.\d+)?\}\}/g);
  return [...new Set([...hits].map(match => match[1]))];
}
