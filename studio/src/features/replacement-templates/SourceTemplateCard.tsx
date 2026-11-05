import { useEffect, useState } from 'react';
import { Check, Film, Play } from 'lucide-react';
import { useArchivedMedia } from '@/entities/asset/api/archive.queries';
import { archiveStreamUrl } from '@/entities/asset/model/archive.types';
import type { ArchivedAsset } from '@/entities/asset/model/archive.types';
import { ArchivedThumbnail } from '@/entities/asset/ui/ArchivedMedia';

function HoverPreview({ video }: { video: ArchivedAsset }) {
  const { data, error } = useArchivedMedia(archiveStreamUrl(video.url));
  const [url, setUrl] = useState('');
  useEffect(() => {
    if (!data) return;
    const source = URL.createObjectURL(data);
    setUrl(source);
    return () => URL.revokeObjectURL(source);
  }, [data]);
  if (error) return <span className="replacement-card-preview-error">预览加载失败</span>;
  return url ? <video src={url} autoPlay muted loop playsInline aria-label={`${video.name}预览`}><track kind="captions" /></video> : null;
}

export function SourceTemplateCard({ video, selected, onToggle }: { video: ArchivedAsset; selected: boolean; onToggle: () => void }) {
  const [preview, setPreview] = useState(false);
  const duration = video.duration ?? 0;
  const valid = duration >= 1 && duration <= 15 && video.hasAudio;
  const reason = !video.hasAudio ? '缺少音轨，暂不可用' : '超出 1–15 秒范围，暂不可用';
  // 事件挂在卡片外层：按钮 disabled 时不派发鼠标事件，异常原片也需要能悬停查看。
  return <article className={`replacement-template-card${selected ? ' is-selected' : ''}`}
    onMouseEnter={() => setPreview(true)} onMouseLeave={() => setPreview(false)}
    onFocus={() => setPreview(true)} onBlur={() => setPreview(false)}>
    <button type="button" className="replacement-card-select" aria-label={`选择模板：${video.name}`} aria-pressed={selected} disabled={!valid} title={valid ? undefined : reason} onClick={onToggle}>
      <div className="replacement-card-media">
        {video.previewUrl ? <ArchivedThumbnail url={video.previewUrl} name={video.name} /> : <Film size={34} strokeWidth={1} />}
        {preview && <HoverPreview video={video} />}
        <span className="replacement-card-check">{selected && <Check size={13} />}</span>
        <span className="replacement-card-play"><Play size={18} fill="currentColor" /></span>
        <span className="replacement-card-duration">{duration.toFixed(1)}s</span>
        <span className="replacement-card-hint">{selected ? '已加入替换' : valid ? '悬停预览 · 点击选择' : '悬停预览 · 暂不可用'}</span>
      </div>
      <div className="replacement-card-caption"><div><h3 title={video.name}>{video.name.replace(/\.[^.]+$/, '')}</h3><span>原片</span></div>
        <p>{video.width} × {video.height} · {video.hasAudio ? '含原声音轨' : '无音轨'}</p>
        <p>{valid ? '保留分镜与动作 · 自由替换' : reason}</p>
      </div>
    </button>
  </article>;
}
