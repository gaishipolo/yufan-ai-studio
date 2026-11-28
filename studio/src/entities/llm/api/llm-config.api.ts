import { api } from '@/shared/api/http';
import type {
  FetchModelsIn,
  FetchModelsOut,
  LlmModelOut,
  LlmProviderOut,
  LlmProvidersOut,
  RandomRewriteSettingsIn,
  RandomRewriteSettingsOut,
  RewriteDebugIn,
  RewriteDebugOptionsOut,
  RewriteDebugOut,
  SaveProviderIn,
} from '@/shared/api/schema';

/** LLM 供应商配置 API：密钥明文永不回显，只有掩码与指纹。 */
export const llmConfigApi = {
  list: () => api.get<LlmProvidersOut>('/llm-config/providers'),
  save: (payload: SaveProviderIn) => api.put<LlmProviderOut>('/llm-config/providers', payload),
  remove: (name: string) => api.delete<LlmProvidersOut>(`/llm-config/providers/${name}`),
  /** 重命名供应商：后端同步迁移 env 键、模型前缀与降级链。 */
  rename: (name: string, newName: string) =>
    api.post<LlmProviderOut>(`/llm-config/providers/${name}/rename`, {name: newName}),
  /** 保存默认模型与降级顺序：第一项为默认模型。 */
  saveModelChain: (models: string[]) =>
    api.put<LlmProvidersOut>('/llm-config/model-chain', { models }),
  /** 从网关 GET /models 拉真实模型 id（只读外网请求）；草稿可带未保存的网关/密钥。 */
  fetchModels: (name: string, draft: FetchModelsIn = {}) =>
    api.post<FetchModelsOut>(`/llm-config/providers/${name}/models:fetch`, draft),
  /** 发一条最小请求验文本往返，不是只看 HTTP 200。 */
  test: (name: string, modelId: string = '') =>
    api.post<Record<string, unknown>>(
      `/llm-config/providers/${name}/test?modelId=${encodeURIComponent(modelId)}`,
    ),
  /**
   * 随机改写调试：路由挂在模板模块（要读模板并做编译预览），权限沿用
   * `llm_config:write`。只调模型 + 编译，不创建任务。
   */
  debugTemplates: () =>
    api.get<RewriteDebugOptionsOut>('/template-specs/debug/templates'),
  debugRewrite: (payload: RewriteDebugIn) =>
    api.post<RewriteDebugOut>('/template-specs/debug/rewrite', payload, {
      timeout: 60_000,
    }),
  /** 把当前 system prompt / 温度 / 模型保存为生产随机改写配置。 */
  saveRandomSettings: (payload: RandomRewriteSettingsIn) =>
    api.put<RandomRewriteSettingsOut>('/template-specs/debug/settings', payload),
};

export type {
  LlmModelOut,
  LlmProviderOut,
  RandomRewriteSettingsIn,
  RandomRewriteSettingsOut,
  RewriteDebugIn,
  RewriteDebugOptionsOut,
  RewriteDebugOut,
};
