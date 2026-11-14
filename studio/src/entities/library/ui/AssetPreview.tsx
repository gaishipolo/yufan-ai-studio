import { useState } from 'react';
import { Play } from 'lucide-react';
import { useLibraryDetail, useLibraryMedia } from '@/entities/library/api/library.queries';
import { CoverImage } from '@/shared/ui/CoverImage';
import { cn } from '@/shared/lib/cn';

/**
 * 资产预览块（模板展示案例用）。
 *
 * 视频**不能**把 `previewUrl` 塞进 `<video src>`：视频资产的那个字段是
 * `/thumbnail`，是一张 JPEG，解码失败就是一块黑 —— 这正是"选视频案例不显示"。
 * `/stream` 也不能直连：它要 Bearer 头，`<video>` 发媒体请求时不带，结果 401。
 * 所以：先给封面（`coverUrl` 是签名直链，`<img>` 能用），想播再经
 * `useLibraryMedia` 取 blob 转 object URL。
 */
export function AssetPreview({
  assetId,
  className,
  /** 允许播放。列表卡片里关掉：那里只需要"看一眼是什么"，点开详情再播。 */
  playable = true,
  alt = '展示案例',
}: {
  assetId: string;
  className?: string;
  playable?: boolean;
  alt?: string;
}) {
  const detail = useLibraryDetail(assetId);
  const isVideo = detail.data?.kind === 'video';
  const [playing, setPlaying] = useState(false);
  const media = useLibraryMedia(assetId, isVideo && playable && playing);

  if (isVideo && playable && playing) {
    if (!media.url) {
      /* 取流期间继续显示封面，不切黑块：黑块会让人以为又坏了。 */
      return (
        <div className={cn('relative overflow-hidden bg-black', className)}>
          <CoverImage
            src={detail.data?.coverUrl ?? undefined}
            seed={assetId}
            alt={alt}
            className="h-full w-full opacity-60"
          />
          <span className="absolute inset-0 flex items-center justify-center text-[10px] text-white/80">
            正在读取视频…
          </span>
        </div>
      );
    }
    /* 竖屏片塞进横向框里会变成两块大黑边，所以按真实比例给尺寸、居中放：
       横屏差不多占满，竖屏就窄一点，两侧是容器底色而不是黑块。 */
    const ratio =
      detail.data?.width && detail.data?.height
        ? `${detail.data.width} / ${detail.data.height}`
        : '16 / 9';
    return (
      <div className={cn('flex items-center justify-center', className)}>
        <video
          src={media.url}
          poster={detail.data?.coverUrl ?? undefined}
          controls
          autoPlay
          playsInline
          style={{aspectRatio: ratio}}
          aria-label={alt}
          className="h-full max-w-full rounded-lg bg-black object-contain"
        >
          <track kind="captions" />
        </video>
      </div>
    );
  }

  const cover = detail.data?.coverUrl ?? undefined;
  const playableVideo = isVideo && playable;
  return (
    <div className={cn('relative overflow-hidden', className)}>
      <CoverImage src={cover} seed={assetId} alt={alt} className="h-full w-full" />
      {playableVideo && (
        <button
          type="button"
          onClick={() => setPlaying(true)}
          aria-label="播放案例视频"
          className="absolute inset-0 flex items-center justify-center bg-black/25 transition-colors hover:bg-black/35"
        >
          <span className="flex h-8 w-8 items-center justify-center rounded-full bg-white/90 text-ink shadow">
            <Play size={14} className="translate-x-[1px]" />
          </span>
        </button>
      )}
      {isVideo && !playable && (
        <span className="absolute bottom-1.5 right-1.5 rounded bg-black/55 px-1 py-0.5 text-[10px] text-white">
          视频
        </span>
      )}
    </div>
  );
}
