import { useState } from 'react';
import { useReplacementCatalog } from '@/entities/template/api/replacement.queries';
import { ArchivedMedia } from '@/entities/asset/ui/ArchivedMedia';
import { Modal } from '@/shared/ui';

export function ArchivedResults() {
  const {data} = useReplacementCatalog();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = data?.runs.find(run => run.id === selectedId);
  const results = data?.runs.filter(run => run.outputUrl) ?? [];
  return <section className="border-t border-line pt-5">
    <h2 className="text-sm font-semibold text-ink">生成结果归档 · {results.length} 份</h2>
    <p className="my-2 text-xs text-ink-mute">模型原始输出保存在每次调用的 outputs 中，输入与结果可对应追溯。生成完成后仍需人工验收。</p>
    {!results.length ? <p className="py-3 text-xs text-ink-mute">暂无已下载的生成结果。</p> :
      <div className="grid gap-3 md:grid-cols-3">{results.map(run => <button key={run.id} onClick={() => setSelectedId(run.id)} className="rounded-xl border border-line bg-panel p-4 text-left hover:border-primary">
        <p className="text-sm font-semibold text-ink">{run.target}</p><p className="mt-2 break-all text-xs text-ink-mute">{run.archivePath}\outputs\generated.mp4</p>
      </button>)}</div>}
    <Modal open={!!selected} onClose={() => setSelectedId(null)} title={selected?.target}>
      {selected?.outputUrl && <ArchivedMedia url={selected.outputUrl} type="video" name={`${selected.target}-${selected.id}.mp4`} />}
    </Modal>
  </section>;
}
