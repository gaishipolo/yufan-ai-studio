import { useState } from 'react';
import { useManageReplacement } from '@/entities/template/api/replacement.queries';
import type { ReplacementRun } from '@/entities/template/model/replacement.types';
import { Can } from '@/shared/access/Can';
import { Button } from '@/shared/ui';
import { toast } from '@/shared/lib/toast';
import { replacementTaskFullName, replacementTaskName } from '@/entities/template/model/replacementTaskIdentity';

export function ReplacementRunManagement({run}: {run: ReplacementRun}) {
  const mutation = useManageReplacement();
  const [confirming, setConfirming] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  async function manage(restore: boolean) {
    setMessage('');
    setError('');
    try {
      await mutation.mutateAsync({id: run.id, restore});
      setConfirming(false);
      setMessage(restore ? '任务与调用档案已恢复' : '已移入回收站，可随时恢复');
      toast(restore ? '任务与调用档案已恢复' : '已移入回收站，可随时恢复', 'success');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '操作未完成，请刷新记录后核对');
    }
  }
  return <Can permission="template:use"><div className="space-y-2">
    {run.deletedAt ? <Button size="sm" loading={mutation.isPending} onClick={() => {void manage(true);}}>恢复任务</Button> :
      !confirming ? <Button size="sm" onClick={() => {setError(''); setMessage(''); setConfirming(true);}}>移入回收站</Button> :
      <div className="rounded-lg border border-line bg-panel-soft p-3">
        <p title={replacementTaskFullName(run)} className="mb-2 break-words text-xs leading-5 text-ink-soft">将“{replacementTaskName(run)}”移入回收站？原片、素材包、输入快照和成片仍保留。正在执行的生成会继续，此操作不会取消任务或退回积分。</p>
        <div className="flex flex-wrap gap-2"><Button size="sm" disabled={mutation.isPending} onClick={() => setConfirming(false)}>取消</Button><Button size="sm" loading={mutation.isPending} onClick={() => {void manage(false);}}>确认移入回收站</Button></div>
      </div>}
    {error && <p role="alert" className="text-xs text-danger">操作失败：{error}，请重试。</p>}
    {message && <p role="status" className="text-xs text-ok">{message}</p>}
  </div></Can>;
}
