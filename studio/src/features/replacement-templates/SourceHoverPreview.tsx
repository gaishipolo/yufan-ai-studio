import { useEffect, useState } from 'react';
import { useArchivedMedia } from '@/entities/asset/api/archive.queries';
import { archivePreviewUrl, archiveStreamUrl } from '@/entities/asset/model/archive.types';
import type { SourceVideo } from './sourceVideo';

interface SourceHoverPreviewProps { video: SourceVideo }
export function SourceHoverPreview({ video }: SourceHoverPreviewProps) {
  const { data, error } = useArchivedMedia(archivePreviewUrl(archiveStreamUrl(video.url)));
  const [url, setUrl] = useState('');
  useEffect(() => {
    if (!data) {setUrl(''); return;}
    const source = URL.createObjectURL(data);
    setUrl(source);
    return () => URL.revokeObjectURL(source);
  }, [data]);
  if (error) return <span className="replacement-card-preview-error">预览加载失败</span>;
  return url ? <video src={url} autoPlay muted loop playsInline aria-label={`${video.name}预览`}><track kind="captions" /></video> : null;
}
