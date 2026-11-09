import { api } from '@/shared/api/http';
import type {
  AssetTagList,
  AssetViewCreate,
  AssetViewList,
  AssetViewOut,
  TagCatalog,
  TagCreate,
  TagOut,
} from '@/shared/api/schema';

/**
 * 标签与保存视图的客户端。
 *
 * **筛选参数是 `tagIds` 而不是词**：接口只接受 id，收到词会 422。
 * 这个约束要一路守到调用点——拼字符串的那一层就是「自由填写」的入口，
 * 而自由填写三个月后必然出现「正面图 / 正面 / 正视图」三种写法。
 *
 * **多个 id 是 AND 语义**：`极简留白 + 俯拍 + 无人` 是「同时满足三项」。
 * 调用方不要为了"多选更宽松"把数组拆成多次请求再合并——那正好是 OR。
 */
export const tagApi = {
  catalog: () => api.get<TagCatalog>('/asset-tags'),
  create: (payload: TagCreate) => api.post<TagOut>('/asset-tags', payload),

  /**
   * 读一条素材的标签。与 `PUT` 成对：只写不读的话，详情浮层里的标签只能
   * 靠列表接口顺带带回来，而列表一次 200 条、每条都带标签会让响应体大一个量级。
   */
  assetTags: (assetId: string) => api.get<AssetTagList>(`/assets/${assetId}/tags`),

  /** 整体替换一条素材的标签。空数组是「清空」，不是「不改」。 */
  setAssetTags: (assetId: string, tagIds: string[]) =>
    api.put<AssetTagList>(`/assets/${assetId}/tags`, {tagIds}),

  listViews: () => api.get<AssetViewList>('/asset-views'),
  saveView: (payload: AssetViewCreate) => api.post<AssetViewOut>('/asset-views', payload),
  deleteView: (id: string) => api.delete<void>(`/asset-views/${id}`),
};
