/** 模型目录：后端 /models 的资源形状 */

export type Capability = 'video' | 'image' | 'audio';

/** 参数 schema 的最小描述，后端透传供应商声明 */
export interface ParamField {
  /**
   * 后端接受 `int` / `integer`、`float` / `number` 等同义写法（`_coerce_param`
   * 按别名收敛），前端也照这套别名渲染，别只认一种。
   */
  type?:
    | 'int'
    | 'integer'
    | 'float'
    | 'number'
    | 'enum'
    | 'string'
    | 'str'
    | 'text'
    | 'bool'
    | 'boolean';
  min?: number;
  max?: number;
  step?: number;
  default?: unknown;
  options?: Array<string | number>;
  /** 可选：供应商给的中文名，前端优先用它，否则查内置词典 */
  label?: string;
  /** 可选：控件下方的一行说明 */
  description?: string;
  /**
   * 这个参数**由使用模板的人选**，不该由模板作者写死。
   *
   * 典型是计费模式：写死等于模板作者替每个使用者决定了要不要花积分，
   * 而积分是用的人的钱。标了它的参数在封装页不能设置（后端会报错），
   * 在模板库填表时渲染成选择控件。
   */
  userSelectable?: boolean;
}

export interface ModelSpec {
  id: string;
  label: string;
  capability: Capability;
  provider: string;
  description?: string;
  paramSchema: Record<string, ParamField>;
  /**
   * 最多收几张参考图（图生图 / 多图合成），`0` 或缺失 = 纯文生图。
   *
   * 参考图不是 `paramSchema` 里的参数：那边的值只能是一个标量，而参考图是一批
   * 图片。但「要不要显示参考图入口」由模型说了算——给纯文生图模型也画上传框，
   * 用户传了不会被用上，图照样出、只是完全不像参考图。
   */
  maxReferences?: number;
  /**
   * 这个模型能收哪几类参考素材（`image` / `video` / `audio`），空数组 = 纯文生。
   *
   * 与 `maxReferences` 同源：都由**模型**声明。前端据此渲染参考素材的类型
   * 筛选与数量上限，不在界面里写死「只选图片、最多 4 张」——那会把视频
   * 替换模型（要「原片视频 + 参考图 + 音频」）的用户挡在门外。
   */
  referenceKinds?: string[];
  /**
   * **按素材类型分开**的上限，键是 `image` / `video` / `audio`。
   *
   * 与 `maxReferences` 的分工：那个是总数（「5 张图 + 3 段音频 = 8」），
   * 这个是分类的（图 5、音频 3）。只守总数的话，用户加到第 6 张图时界面
   * 还说「还能再加」，提交才吃 400。两个上限都要守。
   *
   * 空或缺省 = 供应商没声明这类限制，退回「只守总数」。
   */
  referenceLimits?: Record<
    string,
    { maxCount?: number; minSide?: number; maxSide?: number }
  >;
  /** 兼容历史模型 id 或旧展示名的别名列表，供匹配存量模板/任务 */
  aliases?: string[];
  isDefault: boolean;
}

export interface ModelListResult {
  items: ModelSpec[];
  total: number;
}

/** 按 id 或历史别名查找模型（如 `libtv-wan3-replacement` → `libtv-wan3.0`）。 */
export function findModelSpec(
  models: readonly ModelSpec[],
  idOrAlias: string | null | undefined,
): ModelSpec | undefined {
  const key = idOrAlias?.trim();
  if (!key) return undefined;
  return models.find((item) => item.id === key || item.aliases?.includes(key));
}
