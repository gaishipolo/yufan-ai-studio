import { useEffect, useState } from 'react';
import { CheckCircle2, Package } from 'lucide-react';
import type { ArchivedAsset, ReplacementPack } from '@/shared/api/schema';
import { useArchivedMedia } from '@/entities/asset/api/archive.queries';
import { archivePreviewUrl } from '@/entities/asset/model/archive.types';
import { cn } from '@/shared/lib/cn';

interface ReplacementPackCardProps {
  pack: ReplacementPack;
  cover?: ArchivedAsset;
  selected: boolean;
  disabled: boolean;
  unavailable: boolean;
  label: string;
  onSelect: () => void;
}

export function ReplacementPackCard({pack, cover, selected, disabled, unavailable, label, onSelect}: ReplacementPackCardProps) {
  const {data, error} = useArchivedMedia(pack.coverUrl ?? (cover ? archivePreviewUrl(cover.previewUrl ?? cover.url) : undefined));
  const [url, setUrl] = useState('');
  useEffect(() => {
    if (!data) {setUrl(''); return;}
    const value = URL.createObjectURL(data);
    setUrl(value);
    return () => URL.revokeObjectURL(value);
  }, [data]);
  return <button type="button" aria-label={`选择${label}：${pack.name}`} aria-pressed={selected}
    disabled={disabled || unavailable} onClick={onSelect} title={unavailable ? '素材包不完整，请先在素材库检查' : pack.name}
    className={cn('relative min-w-0 rounded-lg border bg-panel p-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:cursor-not-allowed disabled:opacity-50', selected ? 'border-primary bg-primary/5 ring-1 ring-primary' : 'border-line hover:border-primary-line')}>
    <span className="relative flex aspect-square items-center justify-center overflow-hidden rounded-lg bg-panel-soft">
      {url && !error ? <img src={url} alt="" loading="lazy" className="h-full w-full object-contain" /> : <span className="flex flex-col items-center gap-2 text-xs text-ink-soft"><Package size={24} aria-hidden /><span>{unavailable ? '素材待完善' : error ? '图片暂不可用' : '加载主图…'}</span></span>}
      {selected && <CheckCircle2 size={20} aria-hidden className="absolute right-1 top-1 rounded-full bg-panel text-primary" />}
    </span>
    <span className="mt-2 block break-words text-center text-xs font-medium text-ink">{pack.name}</span>
  </button>;
}
