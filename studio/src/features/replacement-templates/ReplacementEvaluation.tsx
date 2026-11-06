import { useId, useState } from 'react';
import type { ReplacementRun } from '@/entities/template/model/replacement.types';
import type { ReplacementReviewIn } from '@/shared/api/schema';
import { useReviewReplacement } from '@/entities/template/api/replacement.queries';
import { Can } from '@/shared/access/Can';
import { Button } from '@/shared/ui';
import { FIELD_CLASS, REVIEW_LABELS, REVIEW_REASONS, REVIEW_OPTIONS } from './replacementOptions';

interface ReplacementEvaluationProps { run: ReplacementRun }
export function ReplacementEvaluation({run}: ReplacementEvaluationProps) {
  const id = useId();
  const mutation = useReviewReplacement();
  const [editing, setEditing] = useState(false);
  const [status, setStatus] = useState<ReplacementReviewIn['status']>('accepted');
  const [reasons, setReasons] = useState<NonNullable<ReplacementReviewIn['reasonCodes']>>([]);
  const [comment, setComment] = useState('');
  const [version, setVersion] = useState(0);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const latest = run.review?.version ?? 0;
  const stale = editing && version !== latest;
  function start() {
    setStatus(run.reviewStatus === 'rejected' ? 'rejected' : 'accepted');
    setReasons(run.review?.reasonCodes ?? []);
    setComment(run.review?.comment ?? '');
    setVersion(latest);
    setError(''); setSaved(false); setEditing(true);
  }
  async function save() {
    if (mutation.isPending || stale || (status === 'rejected' && !reasons.length)) return;
    setError('');
    try {
      await mutation.mutateAsync({id: run.id, payload: {status, reasonCodes: status === 'rejected' ? reasons : [], comment, expectedVersion: version}});
      setEditing(false); setSaved(true);
    } catch (reason) {setError(reason instanceof Error ? reason.message : '评价保存失败，请重试');}
  }
  return <section aria-label="人工验收" className="space-y-3 rounded-lg border border-line p-4">
    <h3 className="font-semibold text-ink">人工验收：{REVIEW_LABELS[run.reviewStatus ?? 'pending'] ?? run.reviewStatus}</h3>
    {run.review?.reviewedAt && <p className="text-xs text-ink-soft">评价人：{run.review.reviewedBy} · {new Date(run.review.reviewedAt).toLocaleString()}</p>}
    {run.review?.comment && <p className="whitespace-pre-wrap text-xs text-ink-soft">已保存说明：{run.review.comment}</p>}
    {!!run.review?.reasonCodes?.length && <p className="text-xs text-ink-soft">原因：{run.review.reasonCodes.map(code => REVIEW_REASONS[code]).join('、')}</p>}
    {saved && <p role="status" className="text-xs text-ink">评价已保存。</p>}
    {error && <p role="alert" className="text-xs text-danger">{error}；草稿已保留，请核对最新结论后重试。</p>}
    {editing ? <form className="space-y-3" onSubmit={event => {event.preventDefault(); void save();}}>
      <fieldset disabled={mutation.isPending} className="space-y-3">
        <label htmlFor={`${id}-status`} className="block text-xs text-ink-soft">可用性结论</label>
        <select id={`${id}-status`} className={FIELD_CLASS} value={status} onChange={event => setStatus(event.target.value === 'rejected' ? 'rejected' : 'accepted')}><option value="accepted">可用</option><option value="rejected">不可用</option></select>
        {status === 'rejected' && <fieldset aria-describedby={`${id}-reason-hint`} className="space-y-2"><legend className="text-xs text-ink-soft">不可用原因</legend>
          {REVIEW_OPTIONS.map(({code, label}) => <label htmlFor={`${id}-${code}`} key={code} className="mr-3 inline-flex gap-1 text-xs text-ink-soft"><input id={`${id}-${code}`} aria-describedby={`${id}-reason-hint`} type="checkbox" checked={reasons.includes(code)} onChange={() => setReasons(values => values.includes(code) ? values.filter(value => value !== code) : [...values, code])} />{label}</label>)}
          <p id={`${id}-reason-hint`} className="text-xs text-ink-soft">{!reasons.length ? '请选择至少一个原因。' : '可以选择多个原因。'}</p>
        </fieldset>}
        <label htmlFor={`${id}-comment`} className="block text-xs text-ink-soft">评价说明（选填）</label>
        <textarea id={`${id}-comment`} className={FIELD_CLASS} maxLength={2000} rows={3} value={comment} onChange={event => setComment(event.target.value)} />
        {stale && <div role="alert" className="space-y-2 text-xs text-ink-soft"><p>评价版本已更新，请核对上方最新结论，草稿未覆盖。</p><Button onClick={() => {setVersion(latest); setError('');}}>已核对最新结论，保留草稿</Button></div>}
        <Can permission="template:review"><Button type="submit" variant="primary" loading={mutation.isPending} disabled={stale || (status === 'rejected' && !reasons.length)}>保存评价</Button></Can>
        <Button className="ml-2" onClick={() => setEditing(false)}>取消</Button>
      </fieldset>
    </form> : run.status === 'success' && run.outputUrl ? <Can permission="template:review"><Button onClick={start}>{run.reviewStatus === 'pending' ? '评价成片' : '修改评价'}</Button></Can> : <p className="text-xs text-ink-soft">成片成功保存后可进行人工验收。</p>}
    {!!run.review?.history?.length && <details><summary className="cursor-pointer text-xs text-primary">查看评价修改记录</summary><ul className="mt-2 space-y-2 text-xs text-ink-soft">{run.review.history.map(item => <li key={item.version}>版本 {item.version} · {REVIEW_LABELS[item.status ?? 'pending'] ?? item.status} · {item.reviewedBy ?? '尚未评价'} {item.reviewedAt ?? ''}{item.reasonCodes?.length ? ` · ${item.reasonCodes.map(code => REVIEW_REASONS[code]).join('、')}` : ''}{item.comment && ` · ${item.comment}`}</li>)}</ul></details>}
  </section>;
}
