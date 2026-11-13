import type { ImageFeedItem } from '@/shared/api/schema';
import type { Paginated } from '@/shared/api/types';
import type { TaskListParams, VideoTask } from './types';

export type TaskFeedStatus = 'all' | 'queued' | 'running' | 'completed' | 'failed' | 'attention';
export type TaskFeedSource = 'all' | 'video' | 'image' | 'replacement';
export interface TaskFeedParams extends Omit<TaskListParams, 'status'> {
  source?: TaskFeedSource;
  status?: TaskFeedStatus;
  /** 只取统计，不组装条目：侧栏角标等常驻组件的轮询口径 */
  statsOnly?: boolean;
}
export interface TaskFeedItem {
  key: string;
  id: string;
  source: Exclude<TaskFeedSource, 'all'>;
  title: string;
  status: string;
  group: TaskFeedStatus;
  createdAt: string;
  progress: number;
  sourceName: string;
  outputUrl?: string | null;
  errorMessage?: string | null;
  detailUrl?: string | null;
  video?: VideoTask | null;
  /** 生图任务的详细投影（尺寸 / 张数 / 提示词 / 现签地址），其他来源为 null */
  image?: ImageFeedItem | null;
}
export interface TaskFeedResult extends Paginated<TaskFeedItem> {
  stats: { all: number; running: number; completed: number; failed: number; attention: number };
  canReadAll: boolean;
  warnings: string[];
}
