import { useState } from 'react';
import { Check, Play } from 'lucide-react';
import { archivePreviewUrl, type ArchivedAsset } from '@/entities/asset/model/archive.types';
import { replacementFitness } from '@/entities/asset/model/archive-fit';
import { ArchivedThumbnail } from '@/entities/asset/ui/ArchivedThumbnail';
import { cn } from '@/shared/lib/cn';
import { SourceHoverPreview } from './SourceHoverPreview';
import { Button } from '@/shared/ui';

interface SourceTemplateCardProps {
  video: ArchivedAsset;
  selected: boolean;
  disabled?: boolean;
  badge?: string;
  onToggle: () => void;
  onViewPrompt?: () => void;
}

export function SourceTemplateCard({ video, selected, disabled, badge, onToggle, onViewPrompt }: SourceTemplateCardProps) {
  const [preview, setPreview] = useState(false);
  const duration = video.duration ?? 0;
  const width = video.width ?? 0;
  const height = video.height ?? 0;
  const hasDimensions = width > 0 && height > 0;
  const frame = hasDimensions
    ? Math.abs(width / height - 9 / 16) < 0.005
      ? '9:16 竖屏'
      : width === height ? '方形' : width > height ? '横屏' : '竖屏'
    : '画幅待识别';
  const resolution = hasDimensions ? `原始分辨率：${video.width} × ${video.height} 像素` : undefined;
  /* 与素材库共用同一条判定：两边各写一份，改规则时必然漏掉一边。 */
  const fitness = replacementFitness(video);
  const valid = fitness.ok;
  const reason = fitness.ok ? '' : `${fitness.reason}，暂不可用`;
  // 事件挂在卡片外层：按钮 disabled 时不派发鼠标事件，异常原片也需要能悬停查看。
  return <article className={cn('replacement-template-card', selected && 'is-selected')}
    onMouseEnter={() => setPreview(true)} onMouseLeave={() => setPreview(false)}
    onFocus={() => setPreview(true)} onBlur={() => setPreview(false)}>
    <button type="button" className="replacement-card-select" aria-label={`选择模板：${video.name}`} aria-pressed={selected} disabled={disabled || !valid} title={valid ? undefined : reason} onClick={onToggle}>
      <div className="replacement-card-media">
        <ArchivedThumbnail url={video.previewUrl ? archivePreviewUrl(video.previewUrl) : null} type={video.type} />
        {preview && <SourceHoverPreview video={video} />}
        <span className="replacement-card-check">{selected && <Check size={13} />}</span>
        {badge && <span className="replacement-card-badge">{badge}</span>}
        <span className="replacement-card-play"><Play size={18} fill="currentColor" /></span>
        <span className="replacement-card-duration">{duration.toFixed(1)}s</span>
        <span className="replacement-card-hint">{selected ? '已加入替换' : valid ? '悬停预览 · 点击选择' : '悬停预览 · 暂不可用'}</span>
      </div>
      <div className="replacement-card-caption"><div><h3 title={video.name}>{video.name.replace(/\.[^.]+$/, '')}</h3><span>原片</span></div>
        <p title={resolution}>{frame} · {video.hasAudio ? '含原声音轨' : '无音轨'}</p>
        <p>{valid ? '保留分镜与动作 · 自由替换' : reason}</p>
      </div>
    </button>
    {onViewPrompt && <div className="border-t border-line-soft px-3 py-2"><Button size="sm" variant="ghost" aria-label={`查看提示词：${video.name}`} onClick={onViewPrompt}>查看提示词</Button></div>}
  </article>;
}
