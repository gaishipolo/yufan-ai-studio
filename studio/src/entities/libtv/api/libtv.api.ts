import { api } from '@/shared/api/http';
import type { LibTvAccount, LibTvQueueItem } from '../model/types';

/**
 * LibTV 账号面 API —— 后端 `/model-driver/libtv/*`（只读）。
 *
 * 这三条接口背后是逆向出来的上游接口（见 `api/docs/LIBTV_API.md`）：
 * 额度与消费在 `api2.liblib.art`，进度与排队在 `api.liblib.tv`。
 * 前端不直连上游：登录态在服务端那台机器上，浏览器里拿不到。
 */
export const libtvApi = {
  account: () => api.get<LibTvAccount>('/model-driver/libtv/account'),

  /** 一条请求看完全部任务，不要每个卡片各发一次 */
  progress: (taskIds: string[]) =>
    api.post<{ items: LibTvQueueItem[] }>('/model-driver/libtv/progress', { taskIds }),
};
