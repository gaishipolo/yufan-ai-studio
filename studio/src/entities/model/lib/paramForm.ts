/**
 * 模型参数表单：把 `GET /models` 的 `paramSchema` 翻译成可渲染的控件描述。
 *
 * 这里的核心约束是**不认识具体字段名**：`size` / `count` 只是碰巧出现在当前
 * 驱动里，模型多声明一个 `quality` 或 `seed`，生图页应当自动多一个控件，
 * 而不是等前端发版。写死字段名的话，后端声明了、前端不渲染，用户根本没有
 * 入口去传——接口支持等于空谈。
 *
 * 只做「schema → 控件」的翻译，渲染在 `entities/model/ui/ParamFields`，
 * 提交时的拆分在页面里（哪些键走顶层字段是接口契约，不是 schema 的事）。
 */
import type { ModelSpec, ParamField } from '../model/types';

export type ParamValue = string | number | boolean;
export type ParamKind = 'enum' | 'int' | 'float' | 'bool' | 'string';

export interface ParamOption {
  value: string;
  label: string;
}

export interface ParamControl {
  key: string;
  label: string;
  kind: ParamKind;
  /** 有档位时渲染下拉；空数组表示连续值，渲染数字输入框 */
  options: ParamOption[];
  defaultValue: ParamValue;
  hint?: string;
  /** 数字输入框的原生约束（schema 声明什么就传什么，不在这里编） */
  min?: number;
  max?: number;
  step?: number;
}

/** 超过这么多档位就不做下拉：`seed` 这类字段的范围是 0..2^31-1。 */
const MAX_OPTIONS = 12;

const FIELD_LABELS: Record<string, string> = {
  size: '尺寸',
  count: '数量',
  aspectRatio: '比例',
  durationSec: '时长',
  quality: '质量',
  stylePreset: '风格',
  negativePrompt: '反向提示词',
  steps: '步数',
  seed: '随机种子',
  guidance: '引导强度',
  watermark: '水印',
};

/** 数值的展示单位；没有登记的按裸数字显示。 */
const FIELD_UNITS: Record<string, string> = {
  count: '张',
  durationSec: '秒',
  steps: '步',
};

/** 枚举值的可读标签。命中的才替换，其余原样显示（如 `1024x1024`）。 */
const VALUE_LABELS: Record<string, string> = {
  '9:16': '9:16 竖版',
  '16:9': '16:9 横版',
  '1:1': '1:1 方形',
  '4:3': '4:3 横版',
  '3:4': '3:4 竖版',
  low: '低',
  medium: '中',
  high: '高',
};

const KIND_ALIASES: Record<string, ParamKind> = {
  int: 'int',
  integer: 'int',
  float: 'float',
  number: 'float',
  enum: 'enum',
  string: 'string',
  str: 'string',
  text: 'string',
  bool: 'bool',
  boolean: 'bool',
};

function fieldLabel(key: string, field: ParamField): string {
  // 供应商可以在 schema 里带 label：那是唯一知道「这个参数对用户叫什么」的地方
  return field.label?.trim() || FIELD_LABELS[key] || key;
}

function valueLabel(raw: string | number): string {
  return VALUE_LABELS[String(raw)] ?? String(raw);
}

/** 数值档位的单位后缀；`count` 显示成「2 张」比「2」可读。 */
function numericLabel(key: string, value: number): string {
  const unit = FIELD_UNITS[key];
  return unit ? `${value} ${unit}` : String(value);
}

function kindOf(field: ParamField): ParamKind {
  const declared = field.type ? KIND_ALIASES[field.type] : undefined;
  if (declared) return declared;
  // 没写 type 的旧声明：有 options 当枚举，有 min/max 当整数，否则自由文本
  if (field.options?.length) return 'enum';
  if (field.min != null || field.max != null) return 'int';
  return 'string';
}

function asNumber(value: unknown): number | undefined {
  const num = Number(value);
  return Number.isFinite(num) ? num : undefined;
}

/**
 * 按 min/max/step 展开档位。与视频时长那份 `intRange` 的差别是：
 * 这里**允许 0 与负数**（`seed`、`guidance` 都可能从 0 起），
 * 而张数/时长那份强制下限 1（「0 张」是个必被后端拒的选项）。
 */
function ladderOf(field: ParamField): number[] {
  const min = asNumber(field.min);
  const max = asNumber(field.max);
  if (min === undefined || max === undefined || max < min) return [];

  const step = Math.abs(asNumber(field.step) ?? 1);
  if (step === 0) return [min];

  const count = Math.floor((max - min) / step) + 1;
  if (count > MAX_OPTIONS) return []; // 范围太大：交给数字输入框

  const isFloat = kindOf(field) === 'float' || !Number.isInteger(step);
  const options: number[] = [];
  for (let index = 0; index < count; index += 1) {
    const value = min + index * step;
    options.push(isFloat ? Number(value.toFixed(4)) : value);
  }
  // 上限本身也要能选到（max=12、step=5 → 5 / 10 / 12）
  if (options[options.length - 1] !== max) options.push(max);
  return options;
}

function optionsOf(key: string, field: ParamField, kind: ParamKind): ParamOption[] {
  if (kind === 'enum') {
    return (field.options ?? [])
      .map((item) => String(item))
      .filter((item, index, all) => item !== '' && all.indexOf(item) === index)
      .map((value) => ({ value, label: valueLabel(value) }));
  }
  if (kind === 'int' || kind === 'float') {
    return ladderOf(field).map((value) => ({ value: String(value), label: numericLabel(key, value) }));
  }
  return [];
}

function defaultOf(field: ParamField, kind: ParamKind, options: ParamOption[]): ParamValue {
  if (kind === 'bool') return field.default === true;
  if (kind === 'string') return field.default == null ? '' : String(field.default);

  // enum / int / float：声明的 default 必须真在可选集合里，
  // 否则下拉会显示空白（浏览器把不存在的 value 当成未选中）
  const declared = field.default == null ? '' : String(field.default);
  if (options.some((option) => option.value === declared)) {
    return kind === 'enum' ? declared : Number(declared);
  }
  const first = options[0];
  if (!first) return '';
  return kind === 'enum' ? first.value : Number(first.value);
}

/** 把模型的 paramSchema 翻译成一组控件（顺序即声明顺序，与前端无关） */
export function paramControlsFor(model: ModelSpec | undefined): ParamControl[] {
  if (!model?.paramSchema) return [];
  return Object.entries(model.paramSchema).map(([key, field]) => {
    const kind = kindOf(field);
    const options = optionsOf(key, field, kind);
    const control: ParamControl = {
      key,
      label: fieldLabel(key, field),
      kind,
      options,
      defaultValue: defaultOf(field, kind, options),
    };
    if (field.description) control.hint = field.description;
    if (typeof field.min === 'number') control.min = field.min;
    if (typeof field.max === 'number') control.max = field.max;
    if (typeof field.step === 'number') control.step = field.step;
    return control;
  });
}

/** 每个控件的初值（= schema 声明的默认值） */
export function initialParamValues(controls: ParamControl[]): Record<string, ParamValue> {
  const values: Record<string, ParamValue> = {};
  for (const control of controls) values[control.key] = control.defaultValue;
  return values;
}

function matchesKind(kind: ParamKind, value: ParamValue): boolean {
  if (kind === 'bool') return typeof value === 'boolean';
  if (kind === 'int' || kind === 'float') return typeof value === 'number';
  return typeof value === 'string';
}

/** 有档位的控件要确认旧值还在新档位里（换模型后 count=4 → agnes 只有 1） */
function fitsOptions(control: ParamControl, value: ParamValue): boolean {
  if (!control.options.length) return true;
  return control.options.some((option) => option.value === String(value));
}

/**
 * 合并「用户改动」与「控件默认值」。
 *
 * 用户改动里不属于当前模型的键会被丢掉，类型对不上、或档位已失效的值也会
 * 回落到默认值：换模型后旧值可能已经是非法档位（agnes 只有 1 张、seedream
 * 有 4 张），直接沿用会让用户选到一个必被后端 400 的值，而界面上看起来
 * 一切正常——提交后才在「历史记录」里看到一条失败。
 */
export function resolveParamValues(
  controls: ParamControl[],
  overrides: Record<string, ParamValue>,
): Record<string, ParamValue> {
  const values: Record<string, ParamValue> = {};
  for (const control of controls) {
    const userValue = overrides[control.key];
    const usable =
      userValue !== undefined &&
      matchesKind(control.kind, userValue) &&
      fitsOptions(control, userValue);
    values[control.key] = usable ? userValue : control.defaultValue;
  }
  return values;
}

/** 空值不提交：让后端用 schema 的默认值，而不是收到一个空字符串 */
export function isEmptyParamValue(value: ParamValue | undefined): boolean {
  return value === undefined || value === '';
}

/** 数字输入框的回填：非数字保持空串，交给「空值不提交」处理 */
export function parseNumericInput(raw: string): number | '' {
  if (raw.trim() === '') return '';
  const num = Number(raw);
  return Number.isFinite(num) ? num : '';
}
