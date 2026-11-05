import { api } from '@/shared/api/http';
import type {
  CreateTaskPayload,
  TaskListParams,
  TaskListResult,
  TaskRetryResult,
  VideoTask,
} from '../model/types';

/** 任务领域 API —— 后端契约的唯一入口 */
export const taskApi = {
  list: (params: TaskListParams) => api.get<TaskListResult>('/tasks', { ...params }),

  detail: (id: string) => api.get<VideoTask>(`/tasks/${id}`),

  create: (payload: CreateTaskPayload) => api.post<VideoTask>('/tasks', payload),

  /* 后端回的是 TaskRetryOut（只有 id / status / updatedAt），不是整份任务。
     写成 VideoTask 会让「读 data.title」编译通过、运行时 undefined。 */
  retry: (id: string) => api.post<TaskRetryResult>(`/tasks/${id}/retry`),

  toggleFavorite: (id: string) => api.post<VideoTask>(`/tasks/${id}/favorite`),

  remove: (ids: string[]) => api.delete<{ deleted: number }>('/tasks', { ids }),
};
