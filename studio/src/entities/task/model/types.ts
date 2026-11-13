/** 任务状态机：queued → running → completed | failed */
import type { Paginated } from '@/shared/api/types';

export type TaskStatus = 'queued' | 'running' | 'completed' | 'failed';

export type AspectRatio = '9:16' | '16:9' | '1:1';

/** 视频生成任务（后端 /tasks 资源） */
export interface VideoTask {
  id: string;
  title: string;
  status: TaskStatus;
  /** 模型名，如 Seedance 2.5（后端归一后的可读名） */
  model: string;
  /** 模型目录 id，与 `GET /models` 的 id 对齐；历史数据可能为空 */
  modelId?: string | null;
  /** 视频时长（秒） */
  durationSec: number;
  aspectRatio: AspectRatio;
  /** 风格预设与参考图（创建时提交，详情回显） */
  stylePreset?: string | null;
  /** 参考图的**资产库 v2** 资产 id，顺序即提交顺序 */
  referenceLibraryIds?: string[];
  /** 0-100，仅 running 时有意义 */
  progress: number;
  coverUrl: string;
  /** 生成参数快照，供详情页/重试使用 */
  prompt: string;
  authorId: string;
  authorName: string;
  favorited: boolean;
  downloadable: boolean;
  errorMessage?: string;
  createdAt: string;
  updatedAt: string;
  /** 开始生成时间：进度按「已耗时 / 预计耗时」推算 */
  startedAt?: string;
}

export type TaskSort = 'createdAt_desc' | 'createdAt_asc' | 'title_asc';
export type TaskScope = 'mine' | 'team' | 'all';

export interface TaskListParams {
  status?: TaskStatus | 'all';
  keyword?: string;
  sort?: TaskSort;
  scope?: TaskScope;
  onlyAuthorized?: boolean;
  page?: number;
  pageSize?: number;
}

/**
 * `POST /tasks/{id}/retry` 出参（后端 `TaskRetryOut`）。
 *
 * **不是**整份 `VideoTask`：重试是一个动作，后端只回 `{id, status, updatedAt}`
 * （`task:retry:any` 与 `task:read:all` 是两个开关，替别人重跑一次
 * 不该顺带拿到那份任务的 prompt 与作者）。所以这里单独一个类型，
 * 免得有人按 `VideoTask` 读 `data.title` —— 那会是 `undefined` 且编译通过。
 */
export interface TaskRetryResult {
  id: string;
  /** 重试后的状态，正常为 queued */
  status: TaskStatus;
  /** 状态变更时间（UTC），即重新排队的时间 */
  updatedAt: string;
}

export interface CreateTaskPayload {
  title?: string;
  prompt: string;
  model: string;
  durationSec: number;
  aspectRatio: AspectRatio;
  /** 参考图：资产库 v2 的资产 id，顺序即提交顺序（后端按顺序解析，不是所有权凭证） */
  referenceLibraryIds?: string[];
  /** 提示词 {{Text N}} 对应的文本资产 id，顺序即编号顺序；与参考素材互不混用 */
  textLibraryIds?: string[];
  stylePreset?: string;
}

export const TASK_STATUS_LABEL: Record<TaskStatus, string> = {
  queued: '排队中',
  running: '生成中',
  completed: '已完成',
  failed: '生成失败',
};

/** 状态为「未结束」→ 列表需要继续轮询 */
export const isTaskInFlight = (task: VideoTask): boolean =>
  task.status === 'running' || task.status === 'queued';

/** 各状态任务数（不受状态筛选影响，受搜索/范围影响） */
export interface TaskStats {
  all: number;
  queued: number;
  running: number;
  completed: number;
  failed: number;
}

export interface TaskListResult extends Paginated<VideoTask> {
  stats: TaskStats;
  /** 当前账号是否有 `task:read:all`：没有时后端会把 all/team 降级为「只看自己」 */
  canReadAll: boolean;
}
