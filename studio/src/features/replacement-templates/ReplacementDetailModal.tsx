import { CheckCircle2 } from 'lucide-react';
import { ArchivedMedia } from '@/entities/asset/ui/ArchivedMedia';
import { Can } from '@/shared/access/Can';
import { Button, Modal } from '@/shared/ui';
import { CONTINUE, STATUS, NEXT_ACTION } from './replacementOptions';
import { ReplacementEvaluation } from './ReplacementEvaluation';
import { ReplacementInputSummary } from './ReplacementInputSummary';
import { replacementDownloadName } from './replacementDownloadName';
import type { ReplacementWorkbench } from './useReplacementWorkbench';
import { ReplacementRunManagement } from './ReplacementRunManagement';
import { replacementTaskFullName, replacementTaskName, replacementSourceName } from '@/entities/template/model/replacementTaskIdentity';

interface ReplacementDetailModalProps { model: ReplacementWorkbench }
export function ReplacementDetailModal({ model }: ReplacementDetailModalProps) {
  const {currentDetail, setDetailId, setReviewId, feedback, submitting, submitRun} = model;
  return (
    <Modal open={!!currentDetail} onClose={() => setDetailId(null)} title="调用档案" width={880} className="replacement-detail-modal">
      {currentDetail && <div className="space-y-4 p-5">
        {currentDetail.deletedAt && <p role="status" className="rounded-lg bg-panel-soft p-3 text-sm text-ink-soft">此任务已在回收站，恢复后可继续操作。</p>}
        <p title={replacementTaskFullName(currentDetail)} className="break-words text-sm font-semibold text-ink">{replacementTaskName(currentDetail)} · {STATUS[currentDetail.status] ?? currentDetail.status}</p>
        <p className="break-all text-xs text-ink-soft">任务编号：{currentDetail.id} · 创建时间：{new Date(currentDetail.createdAt).toLocaleString()}</p>
        <p className="break-words text-xs text-ink-soft">原片：{replacementSourceName(currentDetail)}</p>
        <p className="break-all text-xs text-ink-soft">上游生成 ID：{currentDetail.taskId ?? '尚未提交'} · 估价：{currentDetail.estimatedCredits ?? '待查询'} · 实际积分：{currentDetail.credits ?? '待回传'}</p>
        <p className="rounded-lg bg-panel-soft p-3 text-xs text-ink-soft">{currentDetail.status === 'success' && !currentDetail.outputUrl ? '结果文件暂不可用，请刷新任务记录或检查归档盘；文件恢复前无法查看成片和评价。' : NEXT_ACTION[currentDetail.status] ?? '请刷新查看最新状态，状态不明确时先核对记录。'}</p>
        {currentDetail.lastError && <p role="alert" className="rounded-lg bg-danger/5 p-3 text-xs text-danger">{currentDetail.lastError}</p>}
        {feedback?.type === 'error' && <p role="alert" className="text-sm text-danger">{feedback.message}</p>}
        {currentDetail.status === 'success' && currentDetail.outputUrl && <div role="status" className="replacement-feedback is-success"><CheckCircle2 size={19} aria-hidden /><p>成片已生成并保存，可播放和下载原始文件。请核对替换效果，生成成功不代表人工验收通过。</p></div>}
        {currentDetail.outputUrl && <ArchivedMedia url={currentDetail.outputUrl} type="video" name={replacementDownloadName(currentDetail.target, currentDetail.id)} />}
        <ReplacementEvaluation key={currentDetail.id} run={currentDetail} />
        <details><summary className="cursor-pointer text-sm text-primary">查看当次输入和替换范围</summary><ReplacementInputSummary run={currentDetail} /></details>
        <details><summary className="cursor-pointer text-sm text-primary">查看最终提示词与归档路径</summary><p className="break-all text-xs text-ink-soft">{currentDetail.archivePath}</p><pre className="mt-3 max-h-72 overflow-auto whitespace-pre-wrap text-xs leading-6 text-ink-soft">{currentDetail.prompt}</pre></details>
        {!currentDetail.deletedAt && currentDetail.status === 'prepared' && <Can permission="template:use"><Button variant="primary" onClick={() => {setDetailId(null); setReviewId(currentDetail.id);}}>核对替换任务</Button></Can>}
        {!currentDetail.deletedAt && currentDetail.status !== 'prepared' && (CONTINUE.includes(currentDetail.status) || (currentDetail.taskId && !['success', 'failed', 'timeout'].includes(currentDetail.status))) &&
          <Can permission="template:use"><Button variant="primary" loading={submitting} onClick={() => {void submitRun(currentDetail);}}>{currentDetail.taskId ? '继续查询已有任务' : '确认并继续零积分提交'}</Button></Can>}
        <ReplacementRunManagement key={`management-${currentDetail.id}`} run={currentDetail} />
      </div>}
    </Modal>
  );
}
