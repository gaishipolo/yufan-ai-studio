import { useRef, useState } from 'react';
import { usePrepareReplacement } from '@/entities/template/api/replacement.queries';
import { replacementFitness } from '@/entities/asset/model/archive-fit';
import { Can } from '@/shared/access/Can';
import { Button } from '@/shared/ui';
import type { ReplacementWorkbench } from './useReplacementWorkbench';

interface BatchRow { id: string; name: string; state: 'pending' | 'working' | 'success' | 'failed'; runId?: string; error?: string }

/** 批量准备保留逐条结果；每条沿用稳定请求标识，核对和执行仍是独立任务。 */
export function ReplacementBatch({model}: {model: ReplacementWorkbench}) {
  const [selected, setSelected] = useState<Record<string, string>>({});
  const [rows, setRows] = useState<BatchRow[]>([]);
  const [running, setRunning] = useState(false);
  const requests = useRef(new Map<string, string>());
  const completed = useRef(new Map<string, string>());
  const prepare = usePrepareReplacement();
  const lock = useRef(false);
  async function start() {
    if (lock.current || model.busy || model.missing || (!model.isTextScene && !model.selectedPack)) return;
    lock.current = true; setRunning(true); model.setBatchPreparing(true);
    const items: BatchRow[] = Object.entries(selected).map(([id, name]) => ({id, name, state: 'pending'}));
    setRows(items);
    const mark = (id: string, update: Partial<BatchRow>) => setRows(previous => previous.map(row => row.id === id ? {...row, ...update} : row));
    try {
      for (const row of items) {
        if (model.boundTemplate && row.id !== model.sourceId) {
          mark(row.id, {state: 'failed', error: '当前封装功能只适用于绑定的原片，请为该原片单独选择功能'});
          break;
        }
        const config = {channel: model.channel, kind: model.kind, sourceId: row.id, packId: model.isTextScene ? null : model.selectedPack?.id,
          videoTemplateId: model.boundTemplate?.id,
          extraPackIds: model.slots > 1 ? model.slotPacks.slice(1).filter(Boolean) : [],
          extraPackVersions: model.slots > 1 ? model.chosenPacks.slice(1).map(pack => pack.version || 1) : [],
          packVersion: model.isTextScene ? null : model.selectedPack?.version || 1, replacementScope: model.showScope ? model.scopes.join('、') : '',
          ...(model.isTextScene ? {scenePrompt: model.scenePrompt} : {}),
          notes: model.isTextScene ? '' : model.notes, aspectRatio: 'adaptive' as const};
        const key = JSON.stringify(config);
        let saved = completed.current.get(key);
        if (saved && model.catalog.data?.runs.some(run => run.id === saved && run.deletedAt)) {completed.current.delete(key); requests.current.delete(key); saved = undefined;}
        if (saved) {mark(row.id, {state: 'success', runId: saved}); continue;}
        let requestId = requests.current.get(key);
        if (!requestId) {requestId = crypto.randomUUID(); requests.current.set(key, requestId);}
        mark(row.id, {state: 'working'});
        try {
          const run = await prepare.mutateAsync({...config, requestId});
          completed.current.set(key, run.id); mark(row.id, {state: 'success', runId: run.id});
        } catch (error) {
          mark(row.id, {state: 'failed', error: error instanceof Error ? error.message : '准备结果待核对'});
          break;
        }
      }
    } finally {lock.current = false; setRunning(false); model.setBatchPreparing(false);}
  }
  return <Can permission="template:use"><details className="rounded-xl border border-line bg-panel p-4">
    <summary className="cursor-pointer text-sm">批量准备多条原片</summary>
    <div className="mt-3 space-y-3">
      <p className="text-xs text-ink-soft">使用当前目标和替换设置。每条原片独立归档，准备完成后逐条核对并确认生成。可切换原片来源和分页继续选择。</p>
      {model.boundTemplate && <p className="text-xs text-ink-soft">当前封装功能只适用于已绑定的原片；其他原片请单独选择封装功能。</p>}
      <div className="grid gap-2 sm:grid-cols-2">{model.videos.map(video => <label key={video.id} className="text-xs"><input type="checkbox" checked={video.id in selected} disabled={model.busy || !replacementFitness(video).ok} onChange={e => setSelected(previous => {const next = {...previous}; if (e.target.checked) next[video.id] = video.name; else delete next[video.id]; return next;})} /> {video.name}</label>)}</div>
      <p className="text-xs">已选 {Object.keys(selected).length} 条</p>
      <Button loading={running} disabled={model.busy || !Object.keys(selected).length || !!model.missing || !!model.assets.error || !!model.catalog.error} onClick={() => {void start();}}>准备所选原片／继续未完成项</Button>
      <Button disabled={model.busy} onClick={() => {setSelected({}); setRows([]);}}>清空批量选择</Button>
      <ul className="space-y-2 text-xs" aria-live="polite">{rows.map(row => <li key={row.id} className="rounded border border-line p-2">{row.name} · {row.state === 'success' ? '输入已保存' : row.state === 'working' ? '正在准备' : row.state === 'failed' ? '未完成，沿用原请求核对后继续' : '尚未处理'}{row.error && <p role="alert" className="text-danger">{row.error}</p>}{row.runId && <Button disabled={model.busy} size="sm" onClick={() => model.setReviewId(row.runId ?? null)}>核对此任务</Button>}</li>)}</ul>
    </div>
  </details></Can>;
}
