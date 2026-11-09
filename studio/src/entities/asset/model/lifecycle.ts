import type { ChipTone } from '@/shared/ui';

/**
 * 素材的三层状态。**必须分清，不能合成一个枚举**：
 *
 * - `stage`：这张东西在流程里的位置（待确认 / 已采纳 / 已弃用 / 已过期）
 * - `compliance`：内容合规审到哪一步了（待审 / 通过 / 未通过）
 * - `deletedAt`：在不在回收站
 *
 * 合成一个"状态"字段会让「被驳回」与「被删除」共享恢复路径 —— 而它们的
 * 恢复方式完全不同：驳回要重新生成或申诉，删除只是从回收站捞回来。
 * 后端 `lifecycle.py` 是同一套取值，这里的文案要跟着它改。
 */

export const COMPLIANCE_LABELS: Record<string, string> = {
  approved: '合规已通过',
  pending: '合规待审',
  rejected: '合规未通过',
};

export const COMPLIANCE_HINTS: Record<string, string> = {
  approved: '可以放进主体并参与生成。',
  pending: '生成件默认待审：审核通过前不能进主体，也不能发布。',
  rejected: '这条内容未通过审核，详情见审核备注；可重新生成或申诉。',
};

export function complianceTone(status: string | undefined): ChipTone {
  if (status === 'approved') return 'ok';
  if (status === 'rejected') return 'danger';
  // pending 用 default 而不是 warn：待审是常态（每个生成件都从待审开始），
  // 一片黄色告警会让人以为出了问题
  return 'default';
}

export const ORIGIN_LABELS: Record<string, string> = {
  upload: '上传',
  generate: 'AI 生成',
};

export const STAGE_LABELS: Record<string, string> = {
  candidate: '待确认',
  adopted: '已采纳',
  discarded: '已弃用',
  expired: '已过期',
};
