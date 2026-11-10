import { Link } from 'react-router-dom';
import { isReplacementPackAvailable, replacementPackCoverId } from '@/entities/template/model/replacementPack';
import { ErrorState } from '@/shared/ui/ErrorState';
import { Button } from '@/shared/ui';
import { Skeleton } from '@/shared/ui/EmptyState';
import { ReplacementPackCard } from './ReplacementPackCard';
import type { ReplacementWorkbench } from './useReplacementWorkbench';

interface ReplacementPackPickerProps { model: ReplacementWorkbench; hintId: string }

export function ReplacementPackPicker({model, hintId}: ReplacementPackPickerProps) {
  const {catalog, assets, packs, packId, setPackId, busy, template, slots, slotPacks, toggleCharacterPack} = model;
  const multiple = template.kind === 'character' && slots > 1;
  const selectedIds = slotPacks.filter(Boolean);
  const error = catalog.error;
  if (catalog.isLoading && !catalog.data) {
    return <div role="status" aria-label="正在加载素材包" className="grid grid-cols-2 gap-3"><Skeleton className="aspect-square" /><Skeleton className="aspect-square" /></div>;
  }
  const empty = <div className="replacement-reference-empty"><p>暂无可用的{template.short}素材包</p><p>请联系素材维护人员封装并发布后，再刷新这里。</p><Button size="sm" loading={catalog.isFetching} onClick={() => {void catalog.refetch();}}>刷新素材包</Button><p><Link to="/assets">查看素材库 →</Link></p></div>;
  return <section aria-label={`目标${template.short}`} aria-describedby={hintId} className="space-y-3">
    {error && <ErrorState error={error} title="素材包加载失败" onRetry={() => {void catalog.refetch(); void assets.refetch();}} />}
    {!packs.length && !error ? empty : <>
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-ink">选择目标{template.short}</h3>
        {multiple && <span role="status" aria-live="polite" className="text-xs font-medium text-primary">已选 {selectedIds.length}/{slots}</span>}
      </div>
      {multiple && <p className="text-xs leading-relaxed text-ink-soft">按出镜顺序点击 {slots} 张人物卡，序号代表第几位人物。再次点击取消，后续序号自动前移。</p>}
      <div className="grid grid-cols-2 gap-3">{packs.map(pack => {
        const index = selectedIds.indexOf(pack.id);
        return <ReplacementPackCard key={pack.id} pack={pack}
        cover={assets.data?.items.find(asset => asset.id === replacementPackCoverId(pack))}
        selected={multiple ? index >= 0 : packId === pack.id}
        selectionOrder={multiple && index >= 0 ? index + 1 : undefined}
        disabled={busy || !!error || (multiple && index < 0 && selectedIds.length >= slots)}
        unavailable={!isReplacementPackAvailable(pack, assets.data?.items ?? [])}
        label={template.short} onSelect={() => multiple ? toggleCharacterPack(pack.id) : setPackId(pack.id)} />;
      })}</div>
    </>}
    <p id={hintId} className="text-xs leading-relaxed text-ink-soft">{multiple ? selectedIds.length >= slots ? '已选满，取消一项后可选择其他人物。' : `还需选择 ${slots - selectedIds.length} 位人物。` : !packId ? `点击主图选择${template.short}。` : ''}生成时自动使用素材包的完整资料。</p>
  </section>;
}
