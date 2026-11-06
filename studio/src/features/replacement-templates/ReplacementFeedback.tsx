import { AlertTriangle, CheckCircle2, X } from 'lucide-react';
import { cn } from '@/shared/lib/cn';
import type { ReplacementWorkbench } from './useReplacementWorkbench';

interface ReplacementFeedbackProps { model: ReplacementWorkbench }
export function ReplacementFeedback({ model }: ReplacementFeedbackProps) {
  const {feedback, setFeedback, busy, upload, preparing} = model;
  return <>
    {feedback && <div className={cn('replacement-feedback', feedback.type === 'success' ? 'is-success' : 'is-error')} role={feedback.type === 'error' ? 'alert' : 'status'}>
      {feedback.type === 'success' ? <CheckCircle2 size={19} aria-hidden /> : <AlertTriangle size={19} aria-hidden />}
      <p>{feedback.message}</p>
      <button type="button" aria-label="关闭操作反馈" onClick={() => setFeedback(null)}><X size={16} /></button>
    </div>}
    {busy && <p role="status" className="replacement-operation-status">{upload.isPending ? '正在上传并保存素材，请稍候…' : preparing ? '正在归档输入并编译提示词，请稍候…' : '正在处理执行请求，请勿重复提交…'}</p>}
  </>;
}
