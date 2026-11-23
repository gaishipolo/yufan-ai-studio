import { api } from '@/shared/api/http';
import type {
  TemplateCaseCreateIn,
  TemplateCaseListOut,
  TemplateCaseOut,
  TemplateCaseUpdateIn,
} from '@/shared/api/schema';

/**
 * 模板案例（`/template-cases`）。
 *
 * **案例 = 一份模板配置的快照**：发布时把模板的当前草稿拷一份存下，别人可以
 * 拿它复制成新模板。与素材库的「案例池」不是一回事 —— 那边是**一条素材**
 * （卡片封面 / 悬停视频），这边是**一份配置**。
 *
 * ## 三档列表口径
 *
 * - `library`：公共案例库，只有**已发布**的；
 * - `mine`：我发布的（含已下架），用来管理；
 * - `trash`：回收站（删除是软删）。
 *
 * 口径直接决定能看到什么，所以放在参数里而不是前端过滤 —— 前端过滤会漏掉
 * 权限差异（别人的下架案例根本不该进前端的数据）。
 */
export type CaseScope = 'library' | 'mine' | 'trash';

/**
 * 案例状态：与后端 `CASE_STATUSES` 对齐（`published` 可复用 / `offline` 已下架）。
 *
 * 收窄成字面量联合而不是 `string`：非法值会一路走到后端才 422，
 * 而「值写错」在前端就能发现。
 */
export type CaseStatus = 'published' | 'offline';

/* 用 `type` 而不是 `interface`：接口没有隐式索引签名，直接当查询参数传给
   `api.get` 会报「缺索引签名」。 */
export type CaseListParams = {
  scope?: CaseScope;
  status?: CaseStatus;
  category?: string;
  keyword?: string;
  page?: number;
  pageSize?: number;
};

export const caseApi = {
  list: (params: CaseListParams = {}) => api.get<TemplateCaseListOut>('/template-cases', params),
  /** 把模板发布为案例：快照当前草稿。 */
  create: (payload: TemplateCaseCreateIn) => api.post<TemplateCaseOut>('/template-cases', payload),
  /** 只改案例自己的字段（名称 / 描述 / 分类），不碰模板。 */
  update: (id: string, payload: TemplateCaseUpdateIn) =>
    api.patch<TemplateCaseOut>(`/template-cases/${id}`, payload),
  /** 下架：不再出现在复用选择器里，可重新上架。 */
  offline: (id: string) => api.post<TemplateCaseOut>(`/template-cases/${id}/offline`),
  online: (id: string) => api.post<TemplateCaseOut>(`/template-cases/${id}/online`),
  /** 删除是软删，进回收站，可恢复。 */
  remove: (id: string) => api.delete<TemplateCaseOut>(`/template-cases/${id}`),
  restore: (id: string) => api.post<TemplateCaseOut>(`/template-cases/${id}/restore`),
};
