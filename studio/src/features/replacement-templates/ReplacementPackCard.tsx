import { useEffect, useState } from 'react';
import { CheckCircle2, Package } from 'lucide-react';
import type { ReplacementPack } from '@/shared/api/schema';
import { useRemoteBlob } from '@/shared/lib/remoteBlob';
import { cn } from '@/shared/lib/cn';

interface ReplacementPackCardProps {
  pack: ReplacementPack;
  selected: boolean;
  disabled: boolean;
  unavailable: boolean;
  label: string;
  selectionOrder?: number;
  onSelect: () => void;
}

/**
 * 素材包卡片。封面只认 `pack.coverUrl`。
 *
 * v1 那套「另传一张 `ArchivedAsset` 兜底」的入参随 `assets` 表下线一起删了：
 * 服务端回的地址已经带画质版本，前端不必再拼一次 `archivePreviewUrl`。
 */
export function ReplacementPackCard({pack, selected, disabled, unavailable, label, selectionOrder, onSelect}: ReplacementPackCardProps) {
  const {data, error} = useRemoteBlob(pack.coverUrl ?? undefined);
  const [url, setUrl] = useState('');
  useEffect(() => {
    if (!data) {setUrl(''); return;}
    const value = URL.createObjectURL(data);
    setUrl(value);
    return () => URL.revokeObjectURL(value);
  }, [data]);
  return <button type="button" aria-label={`选择${label}：${pack.name}`} aria-pressed={selected}
    disabled={disabled || (unavailable && selectionOrder == null)} onClick={onSelect} title={unavailable ? '素材包不完整，请先在素材库检查' : pack.name}
    className={cn('relative min-w-0 rounded-lg border bg-panel p-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:cursor-not-allowed disabled:opacity-50', selected ? 'border-primary bg-primary/5 ring-1 ring-primary' : 'border-line hover:border-primary-line')}>
    <span className="relative flex aspect-square items-center justify-center overflow-hidden rounded-lg bg-panel-soft">
      {url && !error ? <img src={url} alt="" loading="lazy" className="h-full w-full object-contain" /> : <span className="flex flex-col items-center gap-2 text-xs text-ink-soft"><Package size={24} aria-hidden /><span>{unavailable ? '素材待完善' : error ? '图片暂不可用' : '加载主图…'}</span></span>}
      {selected && (selectionOrder != null
        ? <span aria-label={`出镜顺序 ${selectionOrder}`} className="absolute right-1 top-1 flex h-7 w-7 items-center justify-center rounded-full bg-primary text-sm font-bold text-white shadow-sm ring-2 ring-panel">{selectionOrder}</span>
        : <CheckCircle2 size={20} aria-hidden className="absolute right-1 top-1 rounded-full bg-panel text-primary" />)}
    </span>
    <span className="mt-2 block break-words text-center text-xs font-medium text-ink">{pack.name}</span>
  </button>;
}
