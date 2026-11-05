import { api } from '@/shared/api/http';
import type { Capability, ModelListResult } from '../model/types';

/** 模型目录 API —— 后端契约的唯一入口 */
export const modelApi = {
  list: (capability?: Capability) =>
    api.get<ModelListResult>('/models', capability ? { capability } : undefined),
};
