import type { TemplateCaseOut } from '@/shared/api/schema';

/**
 * 案例的展示口径。
 *
 * 词表只在这里写一份：卡片、详情弹窗、选择器都要显示状态，写三遍就会漂。
 *
 * - `published`：**在公共案例库里**，所有人可见可复用；
 * - `offline`：已下架，只有作者自己看得到（后端按这个口径过滤）。
 */
export const CASE_STATUS_LABELS: Record<string, string> = {
  published: '已发布',
  offline: '已下架',
};

/**
 * 复用后新模板的默认名。
 *
 * 带「副本」后缀：模板名在同一作者名下不允许重复（后端会 409），不带后缀
 * 就会在**保存时**才撞车 —— 用户填了半天才被拒。
 */
export function reuseSpecName(item: TemplateCaseOut): string {
  return `${item.specName || item.specKey} 副本`;
}

/** 快照里的槽位数：案例详情要告诉复用者「这份案例带几个槽位」。 */
export function snapshotSlotCount(config?: Record<string, unknown>): number {
  const slots = config?.slots;
  return Array.isArray(slots) ? slots.length : 0;
}

/** 快照里的模型 id。 */
export function snapshotModel(config?: Record<string, unknown>): string {
  return typeof config?.model === 'string' ? config.model : '';
}

/** 快照里的主提示词（详情里给个开头就够，别把整屏铺满）。 */
export function snapshotPrompt(config?: Record<string, unknown>): string {
  return typeof config?.prompt === 'string' ? config.prompt : '';
}

/** 时间戳 → 本地时间；空值给一个占位符而不是「Invalid Date」。 */
export function formatTime(value?: string | null): string {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString();
}
