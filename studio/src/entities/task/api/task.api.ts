import { api } from '@/shared/api/http';
import type {
  CreateTaskPayload,
  TaskListParams,
  TaskListResult,
  VideoTask,
} from '../model/types';

/** 任务领域 API —— 后端契约的唯一入口 */
export const taskApi = {
  list: (params: TaskListParams) => api.get<TaskListResult>('/tasks', { ...params }),

  detail: (id: string) => api.get<VideoTask>(`/tasks/${id}`),

  create: (payload: CreateTaskPayload) => api.post<VideoTask>('/tasks', payload),

  retry: (id: string) => api.post<VideoTask>(`/tasks/${id}/retry`),

  toggleFavorite: (id: string) => api.post<VideoTask>(`/tasks/${id}/favorite`),

  remove: (ids: string[]) => api.delete<{ deleted: number }>('/tasks', { ids }),
};
