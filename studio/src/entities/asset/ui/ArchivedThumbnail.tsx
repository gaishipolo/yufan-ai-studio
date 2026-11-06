import { useEffect, useState } from 'react';
import { Film, Image as ImageIcon, Music } from 'lucide-react';
import { useArchivedMedia } from '../api/archive.queries';

interface ArchivedThumbnailProps {
  /** 缩略图或可以当封面用的原件地址；为空表示这条素材没有封面。 */
  url?: string | null;
  type?: string;
}

/**
 * 列表缩略图。三种情况必须长得不一样：
 * 没有封面（正常，例如音频）、封面读不到（异常）、正在读（短暂）。
 * 全部混成「加载预览…」会让用户以为列表一直在转。
 *
 * 这里不放重试按钮：卡片本身已经是 <button>，嵌套交互元素既非法也会打乱键盘顺序。
 * 重试入口在详情弹窗。
 */
export function ArchivedThumbnail({url, type = 'image'}: ArchivedThumbnailProps) {
  const {data, error} = useArchivedMedia(url ?? undefined);
  const [source, setSource] = useState('');
  const [broken, setBroken] = useState(false);
  useEffect(() => {
    setBroken(false);
    if (!data) {setSource(''); return;}
    const value = URL.createObjectURL(data);
    setSource(value);
    return () => URL.revokeObjectURL(value);
  }, [data]);
  const icon = type === 'video' ? <Film size={26} /> : type === 'audio' ? <Music size={26} /> : <ImageIcon size={26} />;
  if (!url) return <span aria-hidden className="text-primary">{icon}</span>;
  if (error || broken) return <span className="flex flex-col items-center gap-1 text-[11px] text-ink-mute">{icon}预览不可用</span>;
  if (!source) return <span className="text-xs text-ink-mute">加载预览…</span>;
  return <img src={source} alt="" loading="lazy" onError={() => setBroken(true)} className="h-full w-full rounded-lg object-cover" />;
}
