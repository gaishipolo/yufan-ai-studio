import type { ReplacementRun } from '@/entities/template/model/replacement.types';
import { ArchivedMedia } from '@/entities/asset/ui/ArchivedMedia';
import { ArchivedThumbnail } from '@/entities/asset/ui/ArchivedThumbnail';

interface ReplacementInputSummaryProps { run: ReplacementRun }
export function ReplacementInputSummary({run}: ReplacementInputSummaryProps) {
  const summary = run.inputSummary;
  if (!summary) return <p className="rounded-lg bg-panel-soft p-3 text-xs text-ink-soft">历史档案缺少输入摘要，请展开最终提示词核对。</p>;
  const source = summary.assets?.find(item => item.role === 'source_video');
  const references = summary.assets?.filter(item => item.role !== 'source_video') ?? [];
  const cover = references.find(item => item.id === summary.coverReferenceId) ?? references[0];
  return <div className="space-y-3 text-xs text-ink-soft">
    <p className="break-words"><strong>原片：</strong>{source?.name ?? '未记录'}</p>
    {cover && <figure className="w-36 rounded-lg border border-line p-2">
      <div className="h-28 [&_img]:object-contain"><ArchivedThumbnail url={cover.url} type="image" /></div>
      <figcaption className="mt-2 break-all text-center">{run.target}</figcaption>
    </figure>}
    <p className="break-words"><strong>替换范围：</strong>{run.kind === 'character' ? (summary.wardrobeMode === 'reference' ? '人物及参考服装一起替换' : '只换人物身份，保留原穿搭和商品') : run.kind === 'product' ? summary.replacementScope : '仅替换背景，保留人物、商品及前景交互'}</p>
    {summary.notes && <p className="whitespace-pre-wrap break-words"><strong>补充要求：</strong>{summary.notes}</p>}
    {source && <details><summary className="cursor-pointer text-primary">查看当次归档原片</summary><ArchivedMedia url={source.url} type="video" name={source.name} /></details>}
  </div>;
}
