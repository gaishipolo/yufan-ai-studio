import { RotateCcw } from 'lucide-react';
import type { ReplacementRun } from '@/entities/template/model/replacement.types';
import { Can } from '@/shared/access/Can';
import { Button } from '@/shared/ui';
import type { ReplacementWorkbench } from './useReplacementWorkbench';
import { RETRY } from './replacementOptions';

export function ReplacementRetry({run, model}: {run: ReplacementRun; model: ReplacementWorkbench}) {
  if (run.deletedAt || !RETRY.includes(run.status)) return null;
  // 没有上游回执的超时不能判断是否已经提交，继续由档案提示用户核对。
  if (run.status === 'timeout' && !run.taskId) return null;
  const {busy, submitting, submitRun, setDetailId, setReviewId} = model;
  return <Can permission="template:use"><div className="flex flex-wrap items-center gap-2">
    <Button size="sm" variant="primary" icon={<RotateCcw size={13} />} disabled={busy} loading={submitting}
      onClick={() => {
        if (run.taskId) {void submitRun(run);}
        else {setDetailId(null); setReviewId(run.id);}
      }}>重试</Button>
    <p className="text-xs text-ink-soft">{run.taskId
      ? '只恢复查询已有上游任务，不重复提交或扣费。'
      : '沿用原任务素材与提示词，核对后按所选计费方式重试。'}</p>
  </div></Can>;
}
