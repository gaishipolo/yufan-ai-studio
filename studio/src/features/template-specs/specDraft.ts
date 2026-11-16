import type {
  SpecSlotIn,
  TemplateSpecIn,
  TemplateSpecOut,
} from '@/shared/api/schema';
import { KIND_LABEL } from '@/entities/library/model/types';

/**
 * 封装编辑器的草稿模型：**直接复用契约类型**。
 *
 * 刻意不另造一套本地模型：槽位字段与契约一一对应，多一层映射就多一处漂移
 * （"页面能过、后端 400"）。这里只提供"从哪来、怎么改"的小工具，不做规则校验
 * —— 规则在 `core.template_spec`（服务端），前端复制一份就会走岔。
 *
 * 唯一的例外是槽位类型本身（见下面的 `SlotType`）：它是产品定死的四种，写死
 * 一份才不会跟着别处动。
 */

/**
 * 槽位类型：图片 / 成片 / 音频 / 文本四种。
 *
 * 单独写一份、不从契约推导：槽位能选哪几种是**产品定的**（不是"库里有什么
 * 就有什么"），写死一份，契约或资产库那边动到别处时不会自己多出一档给用户选。
 */
export type SlotType = 'image' | 'video' | 'audio' | 'text';
export type SlotBinding = NonNullable<SpecSlotIn['binding']>;

/** 槽位类型的取值，顺序即「加槽位」菜单里的顺序。 */
export const SLOT_TYPES: readonly SlotType[] = ['image', 'video', 'audio', 'text'];

/**
 * 契约里的槽位类型 → 上面四种。
 *
 * 认不出的值（历史草稿里可能还留着已经下线的类型）按图片处理：界面先能打开，
 * 真正拦它的是后端 —— 提交时会被 422 拒掉。
 */
export function slotTypeOf(type: string): SlotType {
  return type === 'video' || type === 'audio' || type === 'text' ? type : 'image';
}

/**
 * 提示词的**编写**上限（不是执行上限）。
 *
 * 真值在后端 `api/src/modules/template_pack/models.py::MAX_PROMPT`，
 * 也就是契约里 `TemplateSpecIn.prompt.maxLength`。
 * 界面上用它显示「字数 N / 上限」，别把它跟 `EXECUTION_PROMPT_LIMIT`（7000，
 * 执行期的硬闸）混成一个数 —— 编写上限远大于它，就是为了能存长草稿。
 *
 * 漂移守卫在 `specDraft.test.ts`：对着 `api/docs/openapi.json` 读一遍 maxLength。
 */
export const MAX_PROMPT = 20000;

/**
 * 槽位类型的展示名 = **资产库的 kind 标签**（图片 / 成片 / 音频 / 文本）。
 *
 * 槽位的 `type` 就是资产库的 kind，所以不另写一份词表：库里给 `video` 改名，
 * 这里跟着变。曾经这里写死「视频」、库里叫「成片」，同一个东西两个名字。
 */
export const SLOT_TYPE_LABELS: Record<SlotType, string> = {
  image: KIND_LABEL.image,
  video: KIND_LABEL.video,
  audio: KIND_LABEL.audio,
  text: KIND_LABEL.text,
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
  // 用共享正则判断「提示词里有没有引用这个槽位」：容空格版本，和后端一致。
  const used = slotRefPattern(slot.key).test(prompt);
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

/**
 * 把「使用者可选」的参数从作者设的参数里剔掉。
 *
 * 标了 `userSelectable` 的参数（计费模式）由**用模板的人**在模板库选，作者不该设。
 * 但老草稿里可能已经存了（那时还没这条规矩），换模型后残留也会跟着走 ——
 * 后端预检/发布会直接拒掉，报「计费模式 由使用模板的人来选」。
 *
 * 所以提交前主动剔一次：省得用户改个无关字段就被这条历史遗留卡住。
 */
export function dropUserSelectableParams(
  spec: TemplateSpecIn,
  userSelectableKeys: string[],
): TemplateSpecIn {
  if (!userSelectableKeys.length) return spec;
  const drop = new Set(userSelectableKeys);
  const params = Object.fromEntries(
    Object.entries(spec.params ?? {}).filter(([key]) => !drop.has(key)),
  );
  return {...spec, params};
}

export interface RefChip {
  /** 插入提示词的文本，如 `{{slot:hero}}`。 */
  token: string;
  label: string;
  /** 整组引用：多张时一次写出全部编号。 */
  all: boolean;
  /** 槽位类型：提示词框里画图标用（图标比「成片/图片」这类文字省地方） */
  kind: SlotType;
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
      const name = slot.label?.trim() || `${SLOT_TYPE_LABELS[slotTypeOf(slot.type)]} ${ordinal}`;
      const chips: RefChip[] = [
        {token: `{{slot:${slot.key}}}`, label: name, all: false, kind: slotTypeOf(slot.type)},
      ];
      // 多值槽位额外给一个「全部」引用。
      const max = slot.maxCount ?? 1;
      if (max > 1) {
        chips.push({
          token: `{{slot:${slot.key}*}}`,
          label: `${name}（全部）`,
          all: true,
          kind: slotTypeOf(slot.type),
        });
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
  const pattern = slotRefPattern(key);
  const count = (text.match(pattern) ?? []).length;
  if (count === 0) return {text, count};
  // 引用两边常各带一个空格（插入时补的），删掉后会把两处空格并成一处。
  return {text: text.replace(pattern, '').replace(/[ \t]{2,}/g, ' '), count};
}

/** 提示词里引用了哪些槽位 —— 给编辑器提示"这条引用指向谁"。 */
export function referencedKeys(text: string): string[] {
  const hits = text.matchAll(slotRefPattern());
  return [...new Set([...hits].map(match => match[1]))];
}

/**
 * 提示词里引用某个槽位的正则（`{{slot:key}}` / `{{slot:key*}}` / `{{slot:key.2}}`）。
 *
 * **两边允许空格**（`{{ slot:key }}`）：后端结构校验用的就是容空格版本，编译器也
 * 跟着容了。这里曾经写成不容空格的严格版，于是「带空格引用」在前端被当成"没被
 * 引用"——改槽位类型时会把 key 改名，下一次保存被后端以「引用了不存在的槽位」
 * 拒绝，而界面完全没提这次改名（2026-11-15 审查 3.4）。
 *
 * 只有一处定义：`referencedKeys` / `rekeySlot` / `stripSlotRefs` 全用它，
 * 别再各写一份。
 */
function slotRefPattern(key?: string): RegExp {
  const name = key ? key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') : '[a-z][a-z0-9_]{0,39}';
  return new RegExp(`\\{\\{\\s*slot:${name}(?:\\*|\\.\\d+)?\\s*\\}\\}`, 'g');
}

/**
 * 发布闸门的前端预检：哪些槽位还缺一级标签。
 *
 * 规则与后端 `_check_executable` 同一条：**要让用户填的**图片/视频槽位必须有
 * 一级标签（模板库按「标签 · 槽位名」分组让人挑素材，没有标签只能退化成
 * "全部素材"）。固定槽位、文本、音频、封装都不要求。
 *
 * 放在这里而不是组件里：保存草稿不判、预检与发布都要判，组件只管把结果讲给用户。
 */
export function slotsMissingTag(slots: SpecSlotIn[]): string[] {
  return slots.flatMap((slot, index) => {
    const needsTag =
      (slot.type === 'image' || slot.type === 'video') &&
      (slot.binding ?? 'user') === 'user' &&
      !slot.tagId;
    if (!needsTag) return [];
    // 报给用户的名字用界面上的叫法（展示名，没填就用「类型 + 第几个」）——
    // 别把 `image_1` 这种内部 key 甩到错误提示里。
    return [slot.label?.trim() || `${SLOT_TYPE_LABELS[slotTypeOf(slot.type)]} ${index + 1}`];
  });
}
