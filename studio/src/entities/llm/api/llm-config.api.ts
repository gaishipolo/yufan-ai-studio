import { api } from '@/shared/api/http';
import type {
  FetchModelsOut,
  LlmModelOut,
  LlmProviderOut,
  LlmProvidersOut,
  SaveProviderIn,
} from '@/shared/api/schema';

/** LLM 供应商配置 API：密钥明文永不回显，只有掩码与指纹。 */
export const llmConfigApi = {
  list: () => api.get<LlmProvidersOut>('/llm-config/providers'),
  save: (payload: SaveProviderIn) => api.put<LlmProviderOut>('/llm-config/providers', payload),
  remove: (name: string) => api.delete<LlmProvidersOut>(`/llm-config/providers/${name}`),
  /** 保存默认模型与降级顺序：第一项为默认模型。 */
  saveModelChain: (models: string[]) =>
    api.put<LlmProvidersOut>('/llm-config/model-chain', { models }),
  /** 从网关 GET /models 拉真实模型 id（只读外网请求）。 */
  fetchModels: (name: string) =>
    api.post<FetchModelsOut>(`/llm-config/providers/${name}/models:fetch`),
  /** 发一条最小请求验文本往返，不是只看 HTTP 200。 */
  test: (name: string, modelId: string = '') =>
    api.post<Record<string, unknown>>(
      `/llm-config/providers/${name}/test?modelId=${encodeURIComponent(modelId)}`,
    ),
};

export type { LlmModelOut, LlmProviderOut };
