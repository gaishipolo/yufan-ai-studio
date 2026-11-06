import { Box, Mountain, UserRound } from 'lucide-react';
import type { ReplacementReviewIn } from '@/shared/api/schema';

export { REPLACEMENT_STATUS as STATUS, REVIEW_LABELS } from '@/entities/template/model/replacement.labels';

export const TYPES = [
  {kind: 'character' as const, label: '人物替换', short: '人物', icon: UserRound, description: '替换人物身份，延续原片表演与镜头'},
  {kind: 'product' as const, label: '产品替换', short: '产品', icon: Box, description: '保留创意与动作，展示你的产品'},
  {kind: 'scene' as const, label: '场景替换', short: '场景', icon: Mountain, description: '保留人物和商品，切换背景空间'},
];
/* 替换范围不再是前端常量：以素材包 supportedScopes + 目录接口的 packScopes 为准。 */

export const CONTINUE = ['prepared', 'waiting_free', 'waiting_executor', 'service_error', 'needs_login'];
export const FIELD_CLASS = 'w-full rounded-lg border border-line bg-panel p-2.5 text-sm text-ink outline-none focus:border-primary';
type ReviewReason = NonNullable<ReplacementReviewIn['reasonCodes']>[number];
export const REVIEW_REASONS: Record<ReviewReason, string> = {replacement: '替换不准确', preservation: '保留内容被改动', visual: '画面异常', audio: '音画问题', other: '其他'};
export const REVIEW_OPTIONS: {code: ReviewReason; label: string}[] = [
  {code: 'replacement', label: REVIEW_REASONS.replacement},
  {code: 'preservation', label: REVIEW_REASONS.preservation},
  {code: 'visual', label: REVIEW_REASONS.visual},
  {code: 'audio', label: REVIEW_REASONS.audio},
  {code: 'other', label: REVIEW_REASONS.other},
];
export const NEXT_ACTION: Record<string, string> = {
  preparing: '输入正在归档，请等待准备结束。',
  prepared: '输入已保存，请核对实际素材与提示词，再确认生成。',
  prepare_failed: '检查素材和连接；本页仍保留配置时，可关闭档案后继续准备。刷新后请重新核对配置。',
  queued: '任务已排队，可保留页面等待状态更新。',
  uploading: '正在上传及审核素材，请等待。', uploaded: '素材已上传，等待估价与提交。',
  submitting: '正在确认上游回执，请勿重复提交。', submitted: '上游已受理，等待生成结果。',
  running: '正在生成，可稍后查看结果。', generated: '生成已结束，正在保存成片，下载完成后可查看。',
  waiting_free: '当前报价未满足免费条件；稍后点击继续，仅零积分时才会提交。',
  waiting_executor: '生成账号正在被其他任务使用，系统会按规则等待，请勿重复提交。',
  needs_login: '请在原 LibTV 专用浏览器恢复登录，再点击继续。工作台登录不能替代上游登录。',
  service_error: '请检查上游连接后继续；已有任务只查询，不重复提交。',
  submission_unknown: '提交回执尚不明确，请由管理员核对上游记录；不要重新提交。',
  failed: '请查看失败原因并核对上游记录，当前任务不会直接重新提交。',
  timeout: '查询已超时，请核对上游记录后处理，勿盲目新建相同任务。',
  success: '成片已保存，请播放、下载并完成可用性评价。',
};
