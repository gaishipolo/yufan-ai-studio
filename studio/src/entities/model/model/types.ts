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
}

export interface ModelSpec {
  id: string;
  label: string;
  capability: Capability;
  provider: string;
  description?: string;
  paramSchema: Record<string, ParamField>;
  isDefault: boolean;
}

export interface ModelListResult {
  items: ModelSpec[];
  total: number;
}
