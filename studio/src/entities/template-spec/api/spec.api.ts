import { api } from '@/shared/api/http';
import type {
  SpecCompileIn,
  SpecCompileOut,
  SpecLimitsOut,
  SpecPublishOut,
  SpecTagsIn,
  SpecValidateOut,
  modules__template_pack__spec_schema__TagIn,
  TagOut,
  modules__template_pack__spec_schema__TagParentIn,
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
/** 数组查询参数序列化成 `tagIds=a&tagIds=b`（axios 默认带 [] 会静默不筛选）。 */
const REPEATED_ARRAY = { indexes: null } as const;

export const specApi = {
  list: (params: {status?: string; keyword?: string; tagIds?: string[]; page?: number; pageSize?: number}) =>
    api.get<TemplateSpecListOut>('/template-specs', params, { paramsSerializer: REPEATED_ARRAY }),
  published: (params: {keyword?: string; tagIds?: string[]; page?: number; pageSize?: number}) =>
    api.get<TemplateSpecListOut>('/template-specs/published', params, { paramsSerializer: REPEATED_ARRAY }),
  detail: (key: string) => api.get<TemplateSpecOut>(`/template-specs/${key}`),
  /** 编辑页要显示的上限（存草稿 / 可发布）。真值在后端常量，前端不抄数字。 */
  limits: () => api.get<SpecLimitsOut>('/template-specs/limits'),
  validate: (payload: TemplateSpecIn) =>
    api.post<SpecValidateOut>('/template-specs/validate', payload),
  save: (payload: TemplateSpecIn) => api.post<TemplateSpecOut>('/template-specs', payload),
  publish: (key: string) => api.post<SpecPublishOut>(`/template-specs/${key}/publish`),
  disable: (key: string) => api.post<TemplateSpecOut>(`/template-specs/${key}/disable`),
  /** 回收站。删除是软删（`deletedAt`），可恢复。 */
  trash: (params: {keyword?: string; page?: number; pageSize?: number} = {}) =>
    api.get<TemplateSpecListOut>('/template-specs/trash', params),
  remove: (key: string) => api.delete<TemplateSpecOut>(`/template-specs/${key}`),
  restore: (key: string) => api.post<TemplateSpecOut>(`/template-specs/${key}/restore`),
  compile: (key: string, payload: SpecCompileIn) =>
    api.post<SpecCompileOut>(`/template-specs/${key}/compile`, payload),

  /** 模板独立标签词表（与素材库词表互不影响）。 */
  tags: () => api.get<TagOut[]>('/template-specs/tags'),
  createTag: (payload: modules__template_pack__spec_schema__TagIn) => api.post<TagOut>('/template-specs/tags', payload),
  renameTag: (id: string, payload: modules__template_pack__spec_schema__TagIn) =>
    api.patch<TagOut>(`/template-specs/tags/${id}`, payload),
  moveTag: (id: string, payload: modules__template_pack__spec_schema__TagParentIn) =>
    api.patch<TagOut>(`/template-specs/tags/${id}/parent`, payload),
  deleteTag: (id: string) => api.delete<null>(`/template-specs/tags/${id}`),
  /** 给模板打标签（完整替换，不改草稿、不发版）。 */
  setTags: (key: string, payload: SpecTagsIn) =>
    api.patch<TemplateSpecOut>(`/template-specs/${key}/tags`, payload),
};
