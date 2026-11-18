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
 * 能勾选的模型数量上限。
 *
 * 真值在后端 `api/src/core/template_spec/models.py::MAX_ALLOWED_MODELS`
 * （也就是契约里 `TemplateSpecIn.allowedModels.maxLength`）。
 *
 * 后端超了会 422，界面上却看不出问题 —— 所以这里写死同一个数，
 * 并由 `specDraft.test.ts` 对着契约做漂移守卫。
 */
export const MAX_ALLOWED_MODELS = 10;

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
    filledText: '',
    emptyText: '',
    position,
  };
}

/**
 * 槽位是不是**可选**的。
 *
 * 判据只有一个：`minCount === 0`。**不要另加一个 `required` 字段** ——
 * 两个事实源不同步时，界面显示「必填」而编译器放行（或反过来），
 * 用户看到的与实际生效的不一致，而这类错只在生成结果里显形。
 */
export function isOptionalSlot(slot: SpecSlotIn): boolean {
  return (slot.minCount ?? 1) === 0;
}

/**
 * 这个槽位切成**必填**后，两段文案要清掉吗？
 *
 * **必填 = 素材一定在 = 直接插编号** —— 两段文案（「填了怎么说 / 没填怎么说」）
 * 对必填槽位没有意义：没有「没填」这个状态。后端也会拦（必填槽位带文案直接
 * 400，见 `core/template_spec/rules.py::_validate_slot_texts`）。
 *
 * 返回要清掉的字段；不用清时返回 `null`。
 *
 * **只管「有没有文案要清」，不管「是不是必填」** —— 目标状态由调用方判断
 * （调用方手上才知道切完之后是必填还是可选）。把两件事混在一起判，会出现
 * 「切必填时按切换前的 minCount 判断，于是没清」的这种错。
 *
 * **要清而不是留着**：留着的话，后端会拒（400），而界面看不出原因 ——
 * 作者明明写了文案，点保存却被拒，没人说得清是哪一条规则。
 */
export function textsToClearForRequired(slot: SpecSlotIn): {
  filledText: string;
  emptyText: string;
} | null {
  if (!slot.filledText?.trim() && !slot.emptyText?.trim()) return null;
  return {filledText: '', emptyText: ''};
}

/**
 * 把「必填 / 可选」切换成 `minCount`。
 *
 * 只有 `binding=user` 的槽位能可选：固定槽位的素材在封装时就定死了，
 * 本来就不存在「选不选」。
 *
 * 切回**必填**时顺带清掉两段文案（见 `textsToClearForRequired`）：
 * 必填 = 直接插编号，文案对它没有意义，留着会被后端 400 打回。
 */
export function setSlotOptional(slot: SpecSlotIn, optional: boolean): Partial<SpecSlotIn> {
  if ((slot.binding ?? 'user') === 'fixed') return {};
  if (optional) {
    // 可选 = 最少 0 张。从「必填」切过来时，最少数改成 0；
    // 最多数不能是 0（那就没法选任何东西了），所以至少留 1。
    return {minCount: 0, maxCount: Math.max(1, slot.maxCount ?? 1)};
  }
  return {
    minCount: Math.max(1, slot.minCount ?? 0),
    maxCount: Math.max(1, slot.maxCount ?? 1),
    ...(textsToClearForRequired(slot) ?? {}),
  };
}

export function blankSpec(): TemplateSpecIn {
  return {
    key: newKey('spec'),
    name: '',
    description: '',
    model: '',
    allowedModels: [],
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
      allowedModels: out.latest.allowedModels ?? [],
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
 * 模板能跑的全部模型：**固定模型 + 作者声明的允许清单**（去重、保序）。
 *
 * 固定模型永远在可选项里：它是模板自己的模型，作者没把它写进清单也不该让
 * 使用者无路可走。清单是「再加几款让使用者挑」的声明，所以是并集而不是替换。
 *
 * 与后端 `spec_service.effective_model_ids` 同一口径 —— 两处算法一旦漂移，
 * 就会变成「界面里能选、提交时被拒」。
 */
export function effectiveModelIds(fixedModel: string, allowedModels: string[]): string[] {
  const ids = fixedModel ? [fixedModel] : [];
  for (const modelId of allowedModels) {
    if (modelId && !ids.includes(modelId)) ids.push(modelId);
  }
  return ids;
}

/**
 * 勾选的模型清单 → 契约里的 `model` + `allowedModels`。
 *
 * **第一个勾选的模型就是默认模型**（用户在多选里排的序 = 使用者看到的默认序），
 * 其余进 `allowedModels`。契约里 `model` 是必填的（后端要拿它当模板自己的模型），
 * 所以这里不是「存两个字段」，而是**一个列表拆成两半**。
 *
 * 为什么不让用户管「哪个是默认」：那要两个控件（单选 + 多选），而两个控件会
 * 表达出「模型」和「允许选用的模型」是两件事 —— 其实是一件事（这份模板能用哪
 * 几款模型，以及先拿哪款）。合成一个控件后，用户勾完就完事，顺序即默认序。
 *
 * 与后端的对应关系：`model` = `model`，`allowedModels` = 其余。**列表为空时
 * `model` 为空串**（后端会拒「模型必填」，所以前端要拦，见 `missingDefaultModel`）。
 */
export function splitModelSelection(selection: string[]): {
  model: string;
  allowedModels: string[];
} {
  const ids: string[] = [];
  for (const modelId of selection) {
    if (modelId && !ids.includes(modelId)) ids.push(modelId);
  }
  return {model: ids[0] ?? '', allowedModels: ids.slice(1)};
}

/**
 * 契约里的 `model` + `allowedModels` → 勾选清单（第一个是默认模型）。
 *
 * `splitModelSelection` 的逆运算。已存的老草稿是「单选 + 多选」两段式的，
 * 还原成列表时**固定模型排第一**（它就是默认），清单项追加在后。
 */
export function joinModelSelection(model: string, allowedModels: string[]): string[] {
  return effectiveModelIds(model, allowedModels);
}

/**
 * 把一段文字里指向 `oldKey` 的槽位引用改成 `newKey`。
 *
 * 只改**这个槽位自己**的引用（作者写的是自引用），别的槽位不动。
 * **修饰符要原样保留**：`{{slot:x*}}` 改成 `{{slot:y*}}`，不能变成
 * `{{slot:y}}` —— 那是「列出这组」变成「第一张」，语义变了。
 */
export function rewriteSlotRef(text: string, oldKey: string, newKey: string): string {
  return text.replace(slotRefPattern(oldKey), (raw, _key, modifier) =>
    `{{slot:${newKey}${modifier ?? ''}}}`,
  );
}

/**
 * 改槽位类型时顺带改名 —— **只在没被引用过时**。
 *
 * key 是按类型自动生成的（`image_1`），类型改成视频后还叫 `image_1` 会误导人；
 * 但提示词里可能已经有 `{{slot:image_1}}`，改名等于让那条引用静默失效（编译期
 * 报「引用了不存在的槽位」）。所以：用过就不动，没用过才跟着类型改名。
 *
 * ## 「用过」包括两处，漏一处就留下悬空引用
 *
 * 1. **提示词正文**里的 `{{slot:key}}`；
 * 2. **这个槽位自己的「选中时文案」**里的自引用（`{{slot:key}}`）。
 *
 * 第 2 处以前漏了：改名后文案里的 token 还指着旧 key，而后端
 * `_validate_slot_texts` 规定「选中时文案只能引用自己」—— 引用旧 key
 * 会被判成「引用了别的槽位」，作者保存时被 400 打回，而界面上看不出原因。
 *
 * 正文引用过时**不改名**（改了就让正文的引用静默失效）；
 * 只有文案引用过时**改名并同步改文案里的 token**（文案跟着槽位走，
 * 编号变了文案里的 token 也该变）。
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
  const usedInPrompt = slotRefPattern(slot.key).test(prompt);
  if (usedInPrompt) return slots;
  const next = slots.slice();
  const newKey = nextSlotKey(
    slots.filter((_, position) => position !== index),
    nextType,
  );
  const filledText = slot.filledText ?? '';
  next[index] = {
    ...slot,
    type: nextType,
    key: newKey,
    // 文案里的自引用跟着改名 —— 否则它变成一条指向不存在槽位的引用。
    filledText: rewriteSlotRef(filledText, slot.key, newKey),
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
 * 每个槽位被**哪种写法**引用了。
 *
 * 返回 `{槽位key: Set<形态>}`，形态有三种：
 * - `''` —— 单张引用 `{{slot:x}}`
 * - `'*'` —— 整组引用 `{{slot:x*}}`
 * - `'.2'` —— 第 N 张 `{{slot:x.2}}`
 *
 * **为什么前端也要分形态**：三种写法的「没填素材」语义不同（后端
 * `core/template_spec/compile.py` 的 `_expander` 写明了）：
 * 单张引用走「选中时 / 未选时文案」，整组引用降级为空，第 N 张必然报错。
 * 前端要判断「这个槽位有没有插入点」，就得知道作者写的是不是**单张引用** ——
 * 整组引用没有「一段文案」可言（它是「列出这组」）。
 *
 * 与后端 `core.template_spec.rules.slot_ref_modes` 同一口径。
 */
export function slotRefModes(text: string): Record<string, Set<string>> {
  const modes: Record<string, Set<string>> = {};
  for (const match of text.matchAll(slotRefPattern())) {
    const key = match[1];
    const modifier = match[2] ?? '';
    if (!modes[key]) modes[key] = new Set();
    modes[key].add(modifier);
  }
  return modes;
}

/**
 * 这个槽位在提示词里是不是被**单张引用**了（`{{slot:key}}`）。
 *
 * 「选中时文案」是**替换这个插入点**的文案 —— 没有单张引用就没有插入点，
 * 那段文案写出来也不会进提示词（它是死数据）。
 */
export function hasSingleSlotRef(text: string, key: string): boolean {
  return slotRefModes(text)[key]?.has('') ?? false;
}

/**
 * 「选中时文案」里能引用的**自身**候选。
 *
 * **只列自己** —— 后端规定「选中时文案只能引用自己」（跨槽位引用会让编号
 * 依赖别的槽位的填充量，同一段文案在不同填充下编译出不同结果）。
 * 所以这里不是「所有槽位的候选」，而是这个槽位自己的三种写法：
 *
 * | 候选 | token | 展开成 |
 * |---|---|---|
 * | 单张 | `{{slot:x}}` | 第一张的编号 |
 * | 全部 | `{{slot:x*}}` | 这组的编号（`、{{Mixed 2}}、{{Mixed 3}}`） |
 * | 第 N 张 | `{{slot:x.N}}` | 第 N 张的编号 |
 *
 * 「全部」与「第 N 张」只在**多值槽位**（`maxCount > 1`）上出现：单值槽位
 * 只有一张，没有「整组」也没有「第 2 张」。
 *
 * 候选**算出来**而不是让用户手敲 `{{slot:key}}`：手敲有两个错且都不报错 ——
 * key 拼错要到编译期才 400；槽位改名后引用静默失效。
 */
export function selfRefChips(slot: SpecSlotIn): RefChip[] {
  const name = slot.label?.trim() || `${SLOT_TYPE_LABELS[slotTypeOf(slot.type)]} ${
    (slot.position ?? 0) + 1
  }`;
  const kind = slotTypeOf(slot.type);
  const chips: RefChip[] = [
    {token: `{{slot:${slot.key}}}`, label: `${name}（单张）`, all: false, kind},
  ];
  const max = slot.maxCount ?? 1;
  if (max > 1) {
    chips.push({
      token: `{{slot:${slot.key}*}}`,
      label: `${name}（全部）`,
      all: true,
      kind,
    });
    // 「第 N 张」从 2 起：第 1 张就是「单张」，重复列一份只会让人以为有两个意思。
    for (let index = 2; index <= max; index += 1) {
      chips.push({
        token: `{{slot:${slot.key}.${index}}}`,
        label: `${name}（第 ${index} 张）`,
        all: false,
        kind,
      });
    }
  }
  return chips;
}

/**
 * 提示词里引用某个槽位的正则（`{{slot:key}}` / `{{slot:key*}}` / `{{slot:key.2}}`）。
 *
 * **两边允许空格**（`{{ slot:key }}`）：后端结构校验用的就是容空格版本，编译器也
 * 跟着容了。这里曾经写成不容空格的严格版，于是「带空格引用」在前端被当成"没被
 * 引用"——改槽位类型时会把 key 改名，下一次保存被后端以「引用了不存在的槽位」
 * 拒绝，而界面完全没提这次改名（2026-11-15 审查 3.4）。
 *
 * **两个捕获组**：1 = 槽位 key，2 = 修饰符（`""` / `"*"` / `".2"`）。
 * 修饰符要抓出来，是因为三种写法的语义不同（`slotRefModes` 靠它）。
 * 与后端 `core.template_spec/rules.py::_SLOT_REF` **完全对齐**（同一个形状）。
 *
 * 只有一处定义：`referencedKeys` / `rekeySlot` / `stripSlotRefs` / `slotRefModes`
 * 全用它，别再各写一份。
 */
function slotRefPattern(key?: string): RegExp {
  const name = key ? key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') : '[a-z][a-z0-9_]{0,39}';
  return new RegExp(`\\{\\{\\s*slot:(${name})(\\*|\\.\\d+)?\\s*\\}\\}`, 'g');
}

/**
 * 发布闸门的前端预检：哪些槽位还缺标签。
 *
 * 规则与后端 `_check_executable` 同一条：**要让用户填的**图片/视频槽位必须圈
 * 至少一个标签（模板库按「标签 · 槽位名」分组让人挑素材，没有标签只能退化成
 * "全部素材"）。固定槽位、文本、音频、封装都不要求。
 *
 * 放在这里而不是组件里：保存草稿不判、预检与发布都要判，组件只管把结果讲给用户。
 */
export function slotsMissingTag(slots: SpecSlotIn[]): string[] {
  return slots.flatMap((slot, index) => {
    const needsTag =
      (slot.type === 'image' || slot.type === 'video') &&
      (slot.binding ?? 'user') === 'user' &&
      !slot.tagIds?.length;
    if (!needsTag) return [];
    // 报给用户的名字用界面上的叫法（展示名，没填就用「类型 + 第几个」）——
    // 别把 `image_1` 这种内部 key 甩到错误提示里。
    return [slot.label?.trim() || `${SLOT_TYPE_LABELS[slotTypeOf(slot.type)]} ${index + 1}`];
  });
}
