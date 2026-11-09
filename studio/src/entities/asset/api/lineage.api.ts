import { api } from '@/shared/api/http';
import type { DeriveIntent, DeriveRequest, LineageOut } from '@/shared/api/schema';

/**
 * 生成血缘与一键衍生。
 *
 * 血缘是**图不是树**：`parents`（怎么来的）/ `children`（同样的输入还生出了什么）/
 * `usedBy`（谁在用它）三段分开返回。合成一棵树会暗示"有唯一根"，而实际上
 * 同一张图可以被 20 次生成同时引用，那 20 个结果之间没有任何关系。
 *
 * `derive` **不建任务、不扣费**：它只把"照这个素材再生成一次"的输入位填好，
 * 提交由生成页自己做。所以这里可以放心地在打开浮层时随手点，不会花钱。
 */
export const lineageApi = {
  lineage: (id: string) => api.get<LineageOut>(`/assets/${id}/lineage`),
  derive: (id: string, mode: DeriveRequest['mode']) =>
    api.post<DeriveIntent>(`/assets/${id}/derive`, {mode}),
};
