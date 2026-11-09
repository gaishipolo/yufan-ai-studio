import { api } from '@/shared/api/http';
import type {
  AdoptRequest,
  ArchivedAsset,
  BatchAdoptRequest,
  BatchAdoptResult,
  CandidateList,
} from '@/shared/api/schema';

/**
 * 候选确认的客户端。
 *
 * **候选不是分类**：它们不进 `GET /assets`，只有这一个入口。所以这里没有
 * `scope` / `category` 之类的筛选参数——候选是「待办清单」，待办不筛选位置，
 * 只按批分组（同一 `recipe_id` 的几张必须挨着，否则"刚才那 4 张里选 2 张"没法完成）。
 *
 * 采纳与弃用都**不改字节**：采纳只翻 `stage` 并清掉过期时间，弃用只是标记，
 * 宽限期后由 `asset.gc` 回收。前端因此不需要「删除确认」这种二次弹窗——
 * 误点弃用的代价不是丢文件，而是这张图不再出现在待办里。
 */
export const candidateApi = {
  list: () => api.get<CandidateList>('/assets/candidates'),
  /** 补分类（可空，空则落未分类收件箱）与可选标签。幂等：已采纳再点返回现状。 */
  adopt: (id: string, payload: AdoptRequest = {}) =>
    api.post<ArchivedAsset>(`/assets/${id}/adopt`, payload),
  /** 弃用不删字节；已弃用/已过期的再点会 409，由调用方提示。 */
  discard: (id: string) => api.post<ArchivedAsset>(`/assets/${id}/discard`),
  /**
   * 整组采纳。**全成或全不成**：批量接口没有"部分成功"的返回形态，
   * 因为"选了 8 张成了 5 张"这种结果无法在界面上解释。
   */
  adoptMany: (payload: BatchAdoptRequest) =>
    api.post<BatchAdoptResult>('/assets/batch/adopt', payload),
};
