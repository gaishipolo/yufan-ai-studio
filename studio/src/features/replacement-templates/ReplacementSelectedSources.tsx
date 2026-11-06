import { archivePreviewUrl } from '@/entities/asset/model/archive.types';
import { ArchivedThumbnail } from '@/entities/asset/ui/ArchivedThumbnail';
import { Button } from '@/shared/ui';
import type { ReplacementWorkbench } from './useReplacementWorkbench';

interface ReplacementSelectedSourcesProps { model: ReplacementWorkbench }
export function ReplacementSelectedSources({model}: ReplacementSelectedSourcesProps) {
  const {sourceId, assets, setSourceId, busy} = model;
  if (!sourceId) return null;
  const asset = assets.data?.items.find(item => item.id === sourceId) ?? model.linkedSource?.data;
  return <ul aria-label="已选原片" className="max-h-56 space-y-2 overflow-auto">
      <li className="flex items-center gap-2 rounded-lg border border-line p-2">
        {asset?.previewUrl && <div className="h-12 w-10 shrink-0"><ArchivedThumbnail url={archivePreviewUrl(asset.previewUrl)} type={asset.type} /></div>}
        <span className="min-w-0 flex-1 break-all text-xs text-ink-soft">{asset?.name ?? '正在读取已选原片'}</span>
        <Button size="sm" disabled={busy} aria-label={`移除原片：${asset?.name ?? sourceId}`} onClick={() => setSourceId(null)}>移除</Button>
      </li>
  </ul>;
}
