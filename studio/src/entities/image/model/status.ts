import type { Paginated } from '@/shared/api/types';
import type { ImageJob } from './types';

/** 后端 `ImageJobOut.status` 的四个取值，只有前两个需要继续轮询 */
export const IMAGE_PENDING_STATUSES = new Set(['queued', 'running']);

/** 有未完成记录时每 3 秒问一次，全部落定返回 false，避免空转 */
export const IMAGE_POLL_INTERVAL = 3_000;

/**
 * 老记录 / 老 mock 没有 status 字段，按已完成处理 ——
 * 否则这些历史数据会让轮询永远停不下来。
 */
export function imageStatusOf(job: ImageJob): string {
  return job.status ?? 'succeeded';
}

export function isPendingImage(job: ImageJob): boolean {
  return IMAGE_PENDING_STATUSES.has(imageStatusOf(job));
}

/** 一页里只要还有 queued / running，就要继续轮询 */
export function hasPendingImages(data: Paginated<ImageJob> | undefined): boolean {
  return (data?.items ?? []).some(isPendingImage);
}

/** useQuery 的 refetchInterval 判定收在纯函数里，便于单测而不依赖 Query 内部 API */
export function imagePollInterval(data: Paginated<ImageJob> | undefined): number | false {
  return hasPendingImages(data) ? IMAGE_POLL_INTERVAL : false;
}

/**
 * 成图地址优先用 `outputUrl`（后端落盘的长期地址）。
 * `coverUrl` 可能仍是供应商签名地址，过期后就是裂图。
 * 排队中两者都为空时返回空串，CoverImage 会画渐变占位。
 */
export function imageSrcOf(job: ImageJob): string {
  return job.outputUrl ?? job.coverUrl ?? '';
}

export function clampProgress(value: number | undefined): number {
  if (value == null || !Number.isFinite(value)) return 0;
  return Math.min(100, Math.max(0, Math.round(value)));
}

export interface ImageStatusChip {
  tone: 'default' | 'primary' | 'warn' | 'danger';
  text: string;
  /** 仅 running 有值，用于进度条宽度 */
  progress: number | null;
}

/** 状态角标：把后端枚举翻译成用户能看懂的进度，已完成不显示 */
export function imageStatusChipOf(job: ImageJob): ImageStatusChip | null {
  const status = imageStatusOf(job);
  switch (status) {
    case 'queued':
      return { tone: 'default', text: '排队中', progress: null };
    case 'running': {
      const progress = clampProgress(job.progress ?? undefined);
      return { tone: 'primary', text: `生成中 ${progress}%`, progress };
    }
    case 'failed':
      return { tone: 'danger', text: '生成失败', progress: null };
    case 'succeeded':
      return null;
    default:
      // 后端新增状态时不要静默当成成功，至少让用户看见原始状态
      return { tone: 'warn', text: status, progress: null };
  }
}
