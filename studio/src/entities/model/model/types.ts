/** 模型目录：后端 /models 的资源形状 */

export type Capability = 'video' | 'image' | 'audio';

/** 参数 schema 的最小描述，后端透传供应商声明 */
export interface ParamField {
  type?: 'int' | 'float' | 'enum' | 'string' | 'bool';
  min?: number;
  max?: number;
  step?: number;
  default?: unknown;
  options?: Array<string | number>;
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
