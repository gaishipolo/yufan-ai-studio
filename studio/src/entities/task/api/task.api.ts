import { api } from '@/shared/api/http';
import type { TaskFeedParams, TaskFeedResult } from '../model/feed.types';
import type {
  CreateTaskPayload,
  TaskListParams,
  TaskListResult,
  TaskRetryResult,
  VideoTask,
} from '../model/types';

/** 任务领域 API —— 后端契约的唯一入口 */
export const taskApi = {
  feed: (params: TaskFeedParams) => api.get<TaskFeedResult>('/tasks/feed', { ...params }),
  list: (params: TaskListParams) => api.get<TaskListResult>('/tasks', { ...params }),

  detail: (id: string) => api.get<VideoTask>(`/tasks/${id}`),

  create: (payload: CreateTaskPayload) => api.post<VideoTask>('/tasks', payload),

  /* 后端回的是 TaskRetryOut（只有 id / status / updatedAt），不是整份任务。
     写成 VideoTask 会让「读 data.title」编译通过、运行时 undefined。 */
  retry: (id: string) => api.post<TaskRetryResult>(`/tasks/${id}/retry`),

  toggleFavorite: (id: string) => api.post<VideoTask>(`/tasks/${id}/favorite`),

  /** 批量收藏走 PATCH 而不是 favorite：后者是「切换」，点两次会把已收藏的取消掉 */
  update: (id: string, patch: { title?: string; favorited?: boolean }) =>
    api.patch<VideoTask>(`/tasks/${id}`, patch),

  remove: (ids: string[]) =>
    api.delete<{ deleted: number; skipped: string[] }>('/tasks', { ids }),

  /* 生图与视频是两套记录（`image_jobs` / `video_tasks`），删除也走两个接口：
     任务中心的批量删除按来源分流，别指望后端一个端点认两种 id。 */
  removeImages: (ids: string[]) =>
    api.delete<{ deleted: number; skipped: string[] }>('/images', { ids }),
};
