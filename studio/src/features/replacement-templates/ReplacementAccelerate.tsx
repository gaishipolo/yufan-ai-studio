import { useState } from 'react';
import { Can } from '@/shared/access/Can';
import { Button } from '@/shared/ui';
import type { ReplacementRun } from '@/entities/template/model/replacement.types';
import type { ReplacementWorkbench } from './useReplacementWorkbench';

/** 免费队列报价非 0 时任务会停在 waiting_free，这里给出「用积分插队」的入口。 */
export function ReplacementAccelerate({run, model}: {run: ReplacementRun; model: ReplacementWorkbench}) {
  const [confirming, setConfirming] = useState(false);
  const {submitting, submitRun} = model;
  /* 已提交上游、已删除、已经选了积分队列的任务不再给这个入口：
     前两者改不了计费模式，后者正在用积分跑，再点一次只会重复提交。 */
  if (run.status !== 'waiting_free' || run.taskId || run.deletedAt || run.billingMode === 'credits') return null;
  const credits = run.estimatedCredits;
  const quoted = typeof credits === 'number' && credits > 0;
  if (!confirming) return <Can permission="template:use"><Button size="sm" onClick={() => setConfirming(true)}>{quoted ? `加速生成（约 ${credits} 积分）` : '用积分加速生成'}</Button></Can>;
  return <div className="rounded-lg border border-line bg-panel-soft p-3">
    <p className="mb-2 break-words text-xs leading-5 text-ink-soft">{quoted ? `当前免费队列报价 ${credits} 积分。` : '当前免费队列报价未记录。'}改用积分进入正常队列只影响这一条任务，账号默认开关不变；实际扣费以任务回执为准。</p>
    <div className="flex flex-wrap gap-2"><Button size="sm" disabled={submitting} onClick={() => setConfirming(false)}>取消</Button><Button size="sm" variant="primary" loading={submitting} onClick={() => {void submitRun(run, 'credits');}}>确认用积分加速</Button></div>
  </div>;
}
