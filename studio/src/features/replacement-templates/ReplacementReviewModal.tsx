import { useEffect, useState } from 'react';
import { Can } from '@/shared/access/Can';
import { Button, Modal } from '@/shared/ui';
import type { ReplacementWorkbench } from './useReplacementWorkbench';
import { ReplacementInputSummary } from './ReplacementInputSummary';
import { replacementTaskFullName, replacementTaskName } from '@/entities/template/model/replacementTaskIdentity';

interface ReplacementReviewModalProps { model: ReplacementWorkbench }
export function ReplacementReviewModal({ model }: ReplacementReviewModalProps) {
  const {review, setReviewId, submitting, feedback, submitRun} = model;
  /* 已提交上游的任务只能用原队列查询进度：上游不接受换队列，点了也只会拿到
     409「计费队列不能再改」。这里直接不给勾选项，按钮退回「继续查询进度」。 */
  const submitted = Boolean(review?.taskId);
  const [accelerate, setAccelerate] = useState(false);
  /* 换一条任务核对时跟随档案里的既有选择：加速过的任务再次打开不该悄悄退回免费队列。 */
  useEffect(() => {setAccelerate(!submitted && review?.billingMode === 'credits');}, [review?.id, review?.billingMode, submitted]);
  return (
    <Modal open={!!review} onClose={() => {if (!submitting) setReviewId(null);}} title="核对替换任务" width={760} className="replacement-review-modal">
      <div className="replacement-modal-body space-y-4"><p className="text-sm text-ink-soft">已准备当前原片。核对目标和替换范围，确认后使用所选素材包生成。</p>
        {review && <article key={review.id} className="min-w-0 space-y-3 rounded-xl border border-line p-4"><h3 title={replacementTaskFullName(review)} className="break-words text-sm font-semibold text-ink">{replacementTaskName(review)}</h3><p className="break-all text-xs text-ink-mute">任务编号：{review.id} · 创建时间：{new Date(review.createdAt).toLocaleString()}</p><ReplacementInputSummary run={review} /><details><summary className="cursor-pointer text-xs text-primary">查看最终提示词与归档路径</summary><p className="mt-2 break-all text-xs text-ink-soft">{review.archivePath}</p><pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap break-words text-xs leading-6 text-ink-soft">{review.prompt}</pre></details></article>}
        {!submitted && <label className="flex items-start gap-2 rounded-lg border border-line bg-panel-soft p-3 text-xs leading-5 text-ink-soft"><input type="checkbox" className="mt-0.5" checked={accelerate} disabled={submitting} aria-label="用积分加速生成" onChange={event => setAccelerate(event.target.checked)} /><span><strong className="text-ink">用积分加速</strong><span className="mt-0.5 block text-ink-mute">不使用 0 积分免费队列，按积分报价进入正常队列。只影响这一条任务，账号默认开关和其他任务不变；实际扣费以任务记录里的上游回执为准。</span></span></label>}
        {submitted && <p className="rounded-lg border border-line bg-panel-soft p-3 text-xs leading-5 text-ink-soft">这条任务已经提交上游（任务号 {review?.taskId}），计费队列不能再改。继续只会查询进度；如需用积分生成，请新建一条任务。</p>}
        {feedback?.type === 'error' && <p role="alert" className="text-sm text-danger">{feedback.message}</p>}
      </div>
      <div className="replacement-modal-actions"><p className="text-xs leading-5 text-ink-mute">{submitted ? '已提交的任务只查询进度，不会重复提交。' : accelerate ? '加速按积分报价提交；若积分队列仍报 0，任务会停下等你核对，不会重复扣费。' : '确认后上传原片与参考图，仅在估价为 0 积分时提交。'}</p><div className="flex shrink-0 gap-2"><Button disabled={submitting} onClick={() => setReviewId(null)}>返回调整</Button><Can permission="template:use"><Button variant="primary" loading={submitting} onClick={() => {if (!review) return; void submitRun(review, submitted ? undefined : accelerate ? 'credits' : 'free');}}>{submitted ? '继续查询进度' : accelerate ? '用积分加速' : '确认生成'}</Button></Can></div></div>
    </Modal>
  );
}
