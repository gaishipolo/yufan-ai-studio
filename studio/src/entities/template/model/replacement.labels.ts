/**
 * 替换任务状态与人工验收结论的展示文案。
 *
 * 放在 entities 而不是 features：素材库的结果区也要展示这两套状态，
 * 而 features 之间不允许互相 import（CODE_STYLE 1.2）。
 */
export const REPLACEMENT_STATUS: Record<string, string> = {
  preparing: '正在准备输入', prepare_failed: '准备失败 · 可继续', prepared: '待核对', queued: '排队中', uploading: '上传及审核中',
  uploaded: '已上传', submitting: '提交中', submitted: '已提交', running: '生成中', generated: '下载中',
  success: '成片已保存', waiting_free: '报价非零 · 等待免费', waiting_executor: '等待旧执行器退出',
  submission_unknown: '提交结果未知 · 禁止重提', service_error: '连接待恢复', needs_login: '需要登录',
  failed: '生成失败', timeout: '上游超时',
};

export const REVIEW_LABELS: Record<string, string> = {pending: '待验收', accepted: '可用', rejected: '不可用'};
