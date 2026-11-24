import { api } from '@/shared/api/http';
import type { RefreshOut } from '@/shared/api/schema';

/**
 * 驱动运维 API —— 目前只有「刷新驱动目录」一个动作。
 *
 * 刷新是**进程级**的：只影响被打到的那个实例，多实例靠各自的 5 分钟周期
 * 刷新收敛。这个语义由后端决定，前端不假装「刷新一次全集群都生效」。
 *
 * 审计（谁在何时刷了什么）后端目前只有日志、没有可查询接口，所以页面不提供
 * 历史列表 —— 摆一个查不出东西的入口比没有更糟。
 */
export const driverApi = {
  refresh: () => api.post<RefreshOut>('/model-driver/refresh'),
};
