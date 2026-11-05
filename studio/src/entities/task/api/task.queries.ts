import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { taskApi } from './task.api';
import {
  isTaskInFlight,
  type CreateTaskPayload,
  type TaskListParams,
  type VideoTask,
} from '../model/types';

/** 查询键工厂：所有任务相关缓存都从这里派生，避免失效范围写错 */
export const taskKeys = {
  all: ['tasks'] as const,
  lists: () => [...taskKeys.all, 'list'] as const,
  list: (params: TaskListParams) => [...taskKeys.lists(), params] as const,
  detail: (id: string) => [...taskKeys.all, 'detail', id] as const,
};

const POLL_INTERVAL = 2_500;

/** 任务列表：存在未结束任务时自动轮询，全部完成即停止 */
export function useTaskList(params: TaskListParams) {
  return useQuery({
    queryKey: taskKeys.list(params),
    queryFn: () => taskApi.list(params),
    staleTime: 1_000,
    placeholderData: keepPreviousData,
    refetchInterval: (query) => {
      const data = query.state.data as { items: VideoTask[] } | undefined;
      return data?.items.some(isTaskInFlight) ? POLL_INTERVAL : false;
    },
  });
}

/** 任务详情：与列表保持一致的条件轮询，否则弹窗里的进度会冻结 */
export function useTaskDetail(id: string | null) {
  return useQuery({
    queryKey: taskKeys.detail(id ?? ''),
    queryFn: () => taskApi.detail(id as string),
    enabled: Boolean(id),
    refetchInterval: (query) => {
      const data = query.state.data as VideoTask | undefined;
      return data && isTaskInFlight(data) ? POLL_INTERVAL : false;
    },
  });
}

/** 侧边栏角标用的轻量统计：只取 stats，不拉完整列表 */
export function useTaskStats() {
  return useQuery({
    queryKey: [...taskKeys.all, 'stats'] as const,
    queryFn: () => taskApi.list({ page: 1, pageSize: 1, scope: 'mine' }),
    select: (data) => data.stats,
    refetchInterval: 5_000,
  });
}

/** 新建任务：成功后让列表失效，自动回到第一页最新的位置 */
export function useCreateTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: CreateTaskPayload) => taskApi.create(payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: taskKeys.lists() });
    },
  });
}

export function useRetryTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => taskApi.retry(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: taskKeys.all });
    },
  });
}

export function useToggleFavorite() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => taskApi.toggleFavorite(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: taskKeys.all });
    },
  });
}

export function useDeleteTasks() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (ids: string[]) => taskApi.remove(ids),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: taskKeys.lists() });
    },
  });
}
