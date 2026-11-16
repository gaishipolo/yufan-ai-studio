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
  isDefault: boolean;
}

export interface ModelListResult {
  items: ModelSpec[];
  total: number;
}
