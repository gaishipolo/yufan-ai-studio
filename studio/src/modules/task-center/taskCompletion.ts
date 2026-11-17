import type { TaskFeedStatus } from '@/entities/task/model/feed.types';

/**
 * 「生成完成」提醒的判定，抽成纯函数。
 *
 * 为什么不写在组件里：这条逻辑的正确性全在**边界**上（首次加载不能弹、
 * 同一个任务不能弹两次、上一轮就已经结束的不能再弹、隐藏标签页不能弹），
 * 而这些都是「看着对、实际漏一条或多一条」的那类 bug —— 得能单测。
 *
 * 为什么用 `feed` 而不是 `/tasks`：`/tasks` 只回视频任务，生图任务在另一张表里。
 * 模板库既有图片模板也有视频模板，只盯视频会让「生图完成」永远没有提醒。
 * feed 已经把两者合并，并给了 `group` 这个归一化状态。
 */

export interface TaskSnapshot {
  /** `image:<id>` / `video:<id>` —— 两个来源的 id 可能撞，必须带前缀区分 */
  key: string;
  group: TaskFeedStatus;
  title: string;
}

export interface TaskCompletion {
  key: string;
  /** 只有这两个是终态 */
  group: 'completed' | 'failed';
  title: string;
}

/** 还在路上：排队、生成中，以及「待处理」（等额度 / 登录 / 人工）之后都可能落定 */
const IN_PROGRESS: ReadonlySet<TaskFeedStatus> = new Set<TaskFeedStatus>([
  'queued',
  'running',
  'attention',
]);

export function toSnapshotMap(tasks: readonly TaskSnapshot[]): Map<string, TaskSnapshot> {
  return new Map(tasks.map((task) => [task.key, task]));
}

/**
 * 对比两轮快照，挑出「上一轮还在跑、这一轮落定」的任务。
 *
 * - 上一轮不存在这条 → 是新任务，不算完成（否则刚提交就弹「已完成」）；
 * - 上一轮就已经是终态 → 跳过（翻页 / 重新挂载会造成这种重复展示）；
 * - 现在仍在跑、排队、待处理 → 跳过。
 */
export function completedSince(
  previous: Map<string, TaskSnapshot>,
  current: readonly TaskSnapshot[],
): TaskCompletion[] {
  const done: TaskCompletion[] = [];
  for (const task of current) {
    const before = previous.get(task.key);
    if (!before || !IN_PROGRESS.has(before.group)) continue;
    if (task.group !== 'completed' && task.group !== 'failed') continue;
    done.push({ key: task.key, group: task.group, title: task.title });
  }
  return done;
}

/** 提示文案：标题太长会把 toast 撑成两行，截断由这里统一做。 */
export function completionMessage(done: TaskCompletion, maxTitle = 16): string {
  const title = done.title.trim() || '任务';
  const short = title.length > maxTitle ? `${title.slice(0, maxTitle)}…` : title;
  return done.group === 'completed' ? `「${short}」已完成` : `「${short}」生成失败`;
}
