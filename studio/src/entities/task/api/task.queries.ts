import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { taskApi } from './task.api';
import { useSessionStore } from '@/shared/access/session';
import type { TaskFeedParams, TaskFeedResult } from '../model/feed.types';
import {
  isTaskInFlight,
  type CreateTaskPayload,
  type TaskListParams,
  type VideoTask,
} from '../model/types';

/** 查询键工厂：所有任务相关缓存都从这里派生，避免失效范围写错 */
export const taskKeys = {
  all: ['tasks'] as const,
  feeds: () => [...taskKeys.all, 'feed'] as const,
  feed: (owner: string, params: TaskFeedParams) => [...taskKeys.feeds(), owner, params] as const,
  lists: () => [...taskKeys.all, 'list'] as const,
  list: (params: TaskListParams) => [...taskKeys.lists(), params] as const,
  detail: (id: string) => [...taskKeys.all, 'detail', id] as const,
};

const POLL_INTERVAL = 2_500;
const FEED_POLL_INTERVAL = 5_000;

/** 后台标签页不轮询：React Query 只按窗口焦点暂停，隐藏标签页还要自己挡一层。 */
function pageHidden() {
  return typeof document !== 'undefined' && document.visibilityState === 'hidden';
}

/**
 * 统一任务聚合。默认只读一页，不轮询：
 * 角标这类常驻组件用 `statsOnly`，且没有进行中任务时停止轮询，
 * 避免「每个页面每 5 秒扫一遍归档」（REVIEW_CHECKLIST 反模式 #7 / FE-08）。
 */
export function useTaskFeed(params: TaskFeedParams) {
  const owner = useSessionStore(state => state.subject.id);
  return useQuery({
    queryKey: taskKeys.feed(owner, params),
    queryFn: () => taskApi.feed(params),
    refetchInterval: query => {
      if (pageHidden()) return false;
      const data = query.state.data as TaskFeedResult | undefined;
      return (data?.stats.running ?? 0) > 0 ? FEED_POLL_INTERVAL : false;
    },
  });
}

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

/** 新建任务：成功后让列表失效，自动回到第一页最新的位置 */
export function useCreateTask() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: CreateTaskPayload) => taskApi.create(payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: taskKeys.all });
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
      void qc.invalidateQueries({ queryKey: taskKeys.all });
    },
  });
}
