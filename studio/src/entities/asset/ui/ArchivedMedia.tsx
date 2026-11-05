import { useEffect, useState } from 'react';
import { useArchivedMedia } from '../api/archive.queries';

export function ArchivedMedia({url, type, name}: {url: string; type: string; name: string}) {
  const {data, error} = useArchivedMedia(url);
  const [source, setSource] = useState('');
  useEffect(() => {
    if (!data) {setSource(''); return;}
    const value = URL.createObjectURL(data);
    setSource(value);
    return () => URL.revokeObjectURL(value);
  }, [data]);
  if (error) return <p role="alert" className="text-sm text-danger">素材加载失败</p>;
  if (!source) return <p className="p-4 text-xs text-ink-mute">正在读取归档文件…</p>;
  return <div className="space-y-2">
    {type === 'video' ? <video src={source} controls className="max-h-80 w-full rounded-xl" aria-label={name}><track kind="captions" /></video> :
      type === 'audio' ? <audio src={source} controls aria-label={name}><track kind="captions" /></audio> :
      <img src={source} alt={name} className="max-h-80 w-full rounded-xl object-contain" />}
    <a href={source} download={name} className="inline-block text-xs text-primary underline">下载原始文件</a>
  </div>;
}

export function ArchivedThumbnail({url, name}: {url: string; name: string}) {
  const {data} = useArchivedMedia(url);
  const [source, setSource] = useState('');
  useEffect(() => {
    if (!data) {setSource(''); return;}
    const value = URL.createObjectURL(data);
    setSource(value);
    return () => URL.revokeObjectURL(value);
  }, [data]);
  return source ? <img src={source} alt={name} className="h-full w-full rounded-lg object-cover" /> :
    <span className="text-xs text-ink-mute">加载预览…</span>;
}
