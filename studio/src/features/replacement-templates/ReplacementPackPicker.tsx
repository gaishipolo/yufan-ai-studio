import { Link } from 'react-router-dom';
import { isReplacementPackAvailable, replacementPackCoverId } from '@/entities/template/model/replacementPack';
import { useLibrarySelection } from '@/entities/library/api/library.queries';
import { displayName } from '@/entities/library/model/types';
import { ErrorState } from '@/shared/ui/ErrorState';
import { Button, CoverImage } from '@/shared/ui';
import { Skeleton } from '@/shared/ui/EmptyState';
import { cn } from '@/shared/lib/cn';
import { ReplacementPackCard } from './ReplacementPackCard';
import type { ReplacementWorkbench } from './useReplacementWorkbench';

interface ReplacementPackPickerProps { model: ReplacementWorkbench; hintId: string }

export function ReplacementPackPicker({model, hintId}: ReplacementPackPickerProps) {
  const {catalog, assets, packs, packId, setPackId, busy, template, slots, slotPacks, toggleCharacterPack,
    libraryPackId, setLibraryPackId, libraryPackImages, libraryMode} = model;
  const multiple = template.kind === 'character' && slots > 1;
  const selectedIds = slotPacks.filter(Boolean);
  const error = catalog.error;
  /* 资产库封装（用户 A 自己打的包）只在单人场景出现：多人物模板按槽位各需要一个包，
     而 `libraryPackId` 一次只接一个封装（服务端也这么判）。 */
  const libraryPacks = useLibrarySelection({kind: 'pack', includePacks: true, pageSize: 24, enabled: !multiple});
  if (catalog.isLoading && !catalog.data) {
    return <div role="status" aria-label="正在加载素材包" className="grid grid-cols-2 gap-3"><Skeleton className="aspect-square" /><Skeleton className="aspect-square" /></div>;
  }
  const empty = <div className="replacement-reference-empty"><p>暂无可用的{template.short}素材包</p><p>请联系素材维护人员封装并发布后，再刷新这里。</p><Button size="sm" loading={catalog.isFetching} onClick={() => {void catalog.refetch();}}>刷新素材包</Button><p><Link to="/assets">查看素材库 →</Link></p></div>;
  return <section aria-label={`目标${template.short}`} aria-describedby={hintId} className="space-y-3">
    {error && <ErrorState error={error} title="素材包加载失败" onRetry={() => {void catalog.refetch(); void assets.refetch();}} />}
    {!packs.length && !error && !(libraryPacks.data?.items?.length) ? empty : <>
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-ink">选择目标{template.short}</h3>
        {multiple && <span role="status" aria-live="polite" className="text-xs font-medium text-primary">已选 {selectedIds.length}/{slots}</span>}
      </div>
      {multiple && <p className="text-xs leading-relaxed text-ink-soft">按出镜顺序点击 {slots} 张人物卡，序号代表第几位人物。再次点击取消，后续序号自动前移。</p>}
      {!!packs.length && <div className="grid grid-cols-2 gap-3">{packs.map(pack => {
        const index = selectedIds.indexOf(pack.id);
        return <ReplacementPackCard key={pack.id} pack={pack}
        cover={assets.data?.items.find(asset => asset.id === replacementPackCoverId(pack))}
        selected={multiple ? index >= 0 : packId === pack.id}
        selectionOrder={multiple && index >= 0 ? index + 1 : undefined}
        disabled={busy || !!error || (multiple && index < 0 && selectedIds.length >= slots)}
        unavailable={!isReplacementPackAvailable(pack, assets.data?.items ?? [])}
        label={template.short} onSelect={() => multiple ? toggleCharacterPack(pack.id) : setPackId(pack.id)} />;
      })}</div>}

      {/*
        第二个来源：资产库里 A 自己打的封装（同一产品的三视图 / 细节图）。
        与上面的模板素材包**互斥**——选一个会清掉另一个，服务端也拒绝同时给两路。
        这里的候选只列封装本身（`includePacks`），成员与用途由服务端展开，
        所以卡片上只给名字与张数，不假装知道成员是谁。
      */}
      {!multiple && !!libraryPacks.data?.items?.length && <section aria-label="从资产库选封装" className="space-y-2">
        <div className="flex items-baseline justify-between gap-2">
          <h3 className="text-sm font-semibold text-ink">或从资产库选封装</h3>
          <span className="text-xs text-ink-mute">成员按封装里的顺序当参考图，用途取自成员声明</span>
        </div>
        <div className="grid grid-cols-2 gap-3">{libraryPacks.data.items.map(pack => {
          const selected = libraryPackId === pack.id;
          return <button key={pack.id} type="button" disabled={busy || !!error}
            onClick={() => setLibraryPackId(selected ? '' : pack.id)}
            aria-pressed={selected} aria-label={`资产库封装 ${displayName(pack.name)}`}
            className={cn('rounded-card border p-1.5 text-left transition',
              selected ? 'border-primary bg-primary-soft/40 ring-2 ring-primary/15' : 'border-line-soft bg-panel hover:border-line-strong')}>
            <CoverImage src={pack.coverUrl ?? undefined} seed={pack.id} className="aspect-[4/3] rounded-md" />
            <div className="mt-1 truncate text-[13.5px] text-ink">{displayName(pack.name)}</div>
            <div className="truncate text-[12.5px] text-ink-mute">资产库封装 · 成员在封装页维护</div>
          </button>;
        })}</div>
        {libraryPacks.isPending && <Skeleton className="h-10" />}
        {libraryMode && <p role="status" className="text-xs leading-relaxed text-ink-soft">
          {libraryPackImages.length ? `已选封装共 ${libraryPackImages.length} 张图，准备时按顺序编号。` : '正在读取封装成员…'}
        </p>}
      </section>}
    </>}
    <p id={hintId} className="text-xs leading-relaxed text-ink-soft">{multiple
      ? (selectedIds.length >= slots ? '已选满，取消一项后可选择其他人物。' : `还需选择 ${slots - selectedIds.length} 位人物。`)
      : (packId || libraryMode ? '' : `点击主图选择${template.short}。`)}生成时自动使用素材包的完整资料。</p>
  </section>;
}
