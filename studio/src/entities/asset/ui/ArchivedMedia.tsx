import { useEffect, useState } from 'react';
import { Download } from 'lucide-react';
import { Button } from '@/shared/ui';
import { ErrorState } from '@/shared/ui/ErrorState';
import { useRemoteBlob, useRemoteDownload } from '@/shared/lib/remoteBlob';

interface ArchivedMediaProps {
  /** 原件地址：图片/音频直接预览，视频作为下载来源。 */
  url: string;
  name: string;
  type: string;
  /** 视频播放地址（浏览器兼容的派生流）。不传时退化为播放原件。 */
  previewUrl?: string;
  downloadAllowed?: boolean;
}

/**
 * 归档媒体的预览与下载。
 *
 * 预览走派生流、下载走原件：两者是不同文件，不能共用一个 blob，
 * 否则「下载原始文件」实际拿到的是转码后的版本。
 * 解码失败只说明浏览器放不了，不能推断文件坏了，所以下载入口始终保留。
 */
export function ArchivedMedia({url, name, type, previewUrl, downloadAllowed = true}: ArchivedMediaProps) {
  const playback = type === 'video' && previewUrl ? previewUrl : url;
  const media = useRemoteBlob(playback);
  const download = useRemoteDownload();
  const [source, setSource] = useState('');
  const [decodeError, setDecodeError] = useState(false);
  useEffect(() => {
    setDecodeError(false);
    if (!media.data) {setSource(''); return;}
    const value = URL.createObjectURL(media.data);
    setSource(value);
    return () => URL.revokeObjectURL(value);
  }, [media.data]);
  const downloadButton = downloadAllowed && <div className="space-y-1">
    <Button size="sm" icon={<Download size={13} />} loading={download.isPending}
      onClick={() => download.mutate({url, filename: name})}>下载原始文件</Button>
    {download.error && <p role="alert" className="text-xs text-danger">下载失败：{download.error.message}</p>}
  </div>;
  if (media.error && !source) return <div className="space-y-3">
    <ErrorState error={media.error} title="预览读取失败" onRetry={() => {void media.refetch();}} />
    {downloadButton}
  </div>;
  if (!source) return <div className="space-y-3">
    <p className="p-4 text-xs text-ink-mute">正在读取归档文件…</p>
    {downloadButton}
  </div>;
  return <div className="space-y-2">
    {decodeError && <p role="alert" className="text-xs text-danger">
      当前浏览器无法播放或解码这个文件。原件仍可下载核对，这不代表归档失败。
    </p>}
    {type === 'video'
      ? <video src={source} controls aria-label={name} onError={() => setDecodeError(true)} className="max-h-80 w-full rounded-xl"><track kind="captions" /></video>
      : type === 'audio'
        ? <audio src={source} controls aria-label={name} onError={() => setDecodeError(true)} className="w-full"><track kind="captions" /></audio>
        : <img src={source} alt={name} onError={() => setDecodeError(true)} className="max-h-80 w-full rounded-xl object-contain" />}
    {downloadButton}
  </div>;
}
