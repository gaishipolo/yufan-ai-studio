/**
 * LibTV 账号面（上游接口的投影，不是我们自己的表）。
 *
 * 字段名贴着上游原文：排查时要拿这些值和浏览器里看到的对上，
 * 换一套「我们自己的说法」只会让人两头对不上账。
 */

/** `POST /model-driver/libtv/progress` 的一项 */
export interface LibTvQueueItem {
  taskId: string;
  /** 0 排队/生成中 · 1 生成中 · 2 成功 · 3 失败 */
  status: number;
  percent: number;
  /** UNLIMITED_SERIAL = 0 积分队列排队；RATE_LIMIT = 被上游限流推迟 */
  queueReason: string;
  /** 这台机器在该队列里的位次 */
  queuePosition: number;
  queueTotal: number;
  /** 画布 0 积分队列的名额，默认 10 */
  queueLimit: number;
  power: number;
  benefitTag: string;
  remainSeconds: number;
  failedReason?: string;
  /** 后端拼好的一句话，前端别自己再拼一遍 */
  describe: string;
}

/** 队列位次只对这一个模型有意义（上游是 LibTV 的画布队列） */
export const LIBTV_MODEL_ID = 'libtv-wan3-replacement';

export interface LibTvAsset {
  usablePower: number;
  freeUsablePower: number;
  usedPower: number;
  totalPower: number;
  /** 账号当下还能提交几个任务（**不是**队列名额，别混用） */
  taskCanSubmit: number;
  concurrent: number;
  memberLevel: string;
  expireDate: string;
}

export interface LibTvBenefit {
  modelVersionId?: number | null;
  targetName: string;
  activityName: string;
  /** 权益到期时间：到期后同一批任务会开始扣积分 */
  endTime: string;
  tags: string[];
}

export interface LibTvAccount {
  account: string;
  asset?: LibTvAsset | null;
  freePower: number;
  freePowerExpireAt?: string | null;
  zeroPowerBenefits: LibTvBenefit[];
}

/** 排队中才算「有事可说」：`queueReason` 空串表示真在生成 */
export function isQueued(item: LibTvQueueItem): boolean {
  return Boolean(item.queueReason) && item.status === 0;
}

/**
 * 任务的 `externalId` → 上游 `task_id`。
 *
 * 后端存的是 `<归档工作区>:<上游 task_id>`：前段是归档目录（32 位），后段才是
 * 上游任务号。排队接口只认后段，传整串会拿到空结果——看起来像「查不到排队」，
 * 其实是参数错了。
 */
export function upstreamTaskId(externalId: string | null | undefined): string | null {
  if (!externalId) return null;
  const parts = externalId.split(':');
  return parts.length > 1 && parts[1] ? parts[1] : null;
}

/**
 * 卡片上那一行字。
 *
 * 只有排队/限流才值得占位置：真在生成时百分比已经在旁边了，
 * 再写一句「生成中 48%」是重复信息。
 */
export function queueLabel(item: LibTvQueueItem | null | undefined): string {
  // 没查到（任务还没拿到上游 id、或这一跳查询失败）就当没有排队信息，
  // 不要退回「生成中」——那会让卡片多出一行和百分比重复的字。
  if (!item) return '';
  if (item.queueReason === 'UNLIMITED_SERIAL') {
    // 名额缺失时按画布默认的 10 显示，别渲染成「3/0」
    return `0 积分队列 ${item.queuePosition}/${item.queueLimit || 10}`;
  }
  if (item.queueReason === 'RATE_LIMIT') {
    return item.remainSeconds > 0 ? `上游限流 · 约 ${item.remainSeconds}s` : '上游限流中';
  }
  return '';
}

/** 从进行中的 LibTV 任务集合里抽出可批量查询的上游 taskId 列表。 */
export function inFlightLibtvIds(
  tasks: Array<{ status?: string; modelId?: string | null; externalId?: string | null }>,
): string[] {
  return tasks.flatMap(task => {
    if ((task.status !== 'running' && task.status !== 'queued') || task.modelId !== LIBTV_MODEL_ID) {
      return [];
    }
    const id = upstreamTaskId(task.externalId);
    return id ? [id] : [];
  });
}

/** 合并库内进度与上游实时进度（进行中只增不减且封顶 99，避免闪回）。 */
export function liveTaskProgress(
  taskProgress: number,
  item: LibTvQueueItem | null | undefined,
): number {
  if (!item || item.status > 1) return taskProgress;
  return Math.max(taskProgress, Math.min(99, item.percent));
}
