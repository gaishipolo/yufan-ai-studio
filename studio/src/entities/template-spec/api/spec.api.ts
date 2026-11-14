import { api } from '@/shared/api/http';
import type {
  SpecCompileIn,
  SpecCompileOut,
  SpecPublishOut,
  SpecValidateOut,
  TemplateSpecIn,
  TemplateSpecListOut,
  TemplateSpecOut,
} from '@/shared/api/schema';

/**
 * 模板规格（`docs/TEMPLATE_SPEC.md`）的接口。
 *
 * 路径写字面量，契约守卫才能逐条比对。这里**没有任何本地规则**：槽位合法性、
 * 编号展开、数量上下限全在服务端（`compile` / `validate`），前端只负责把
 * 用户的填写原样发出去 —— 规则只有一份，才不会出现"页面能过、后端 400"。
 */
export const specApi = {
  list: (params: {status?: string; keyword?: string; page?: number; pageSize?: number}) =>
    api.get<TemplateSpecListOut>('/template-specs', params),
  published: (params: {keyword?: string; page?: number; pageSize?: number}) =>
    api.get<TemplateSpecListOut>('/template-specs/published', params),
  detail: (key: string) => api.get<TemplateSpecOut>(`/template-specs/${key}`),
  validate: (payload: TemplateSpecIn) =>
    api.post<SpecValidateOut>('/template-specs/validate', payload),
  save: (payload: TemplateSpecIn) => api.post<TemplateSpecOut>('/template-specs', payload),
  publish: (key: string) => api.post<SpecPublishOut>(`/template-specs/${key}/publish`),
  disable: (key: string) => api.post<TemplateSpecOut>(`/template-specs/${key}/disable`),
  compile: (key: string, payload: SpecCompileIn) =>
    api.post<SpecCompileOut>(`/template-specs/${key}/compile`, payload),
};
