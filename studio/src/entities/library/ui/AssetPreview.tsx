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
  /** 点一下才播（详情/编辑器用）：按钮明确，不会因为鼠标路过就动。 */
  playable = true,
  /** 悬停自动播（列表卡片用）：静音、循环，鼠标移开回到封面。 */
  hoverPlay = false,
  /** 悬停态交给调用方控制。卡片把 mouseenter 挂在**整张卡**上时传它：视频区域只占
   *  卡片上半截，鼠标落在名称/chips 那几十像素上时，自己监听的事件收不到，看起来
   *  就是「卡片不动」。不传则退回自己监听（编辑器里的独立封面用这个）。 */
  active,
  alt = '展示案例',
}: {
  assetId: string;
  className?: string;
  playable?: boolean;
  hoverPlay?: boolean;
  /**
   * 外部控制的悬停态。`undefined` = 自己监听 mouseenter/mouseleave；
   * `true/false` = 完全听调用方的，本组件不再绑事件（否则两套状态会互相覆盖）。
   */
  active?: boolean;
  alt?: string;
}) {
  const detail = useLibraryDetail(assetId);
  const isVideo = detail.data?.kind === 'video';
  const [playing, setPlaying] = useState(false);
  const [hovered, setHovered] = useState(false);
  const hoverNow = active ?? hovered;
  /* 取流只在真的要播时才发：卡片上一排视频各拉一份 blob 是浪费，hover 一次也只发一次
     （`useLibraryMedia` 有 5 分钟缓存）。 */
  const media = useLibraryMedia(
    assetId,
    isVideo && ((playable && playing) || (hoverPlay && hoverNow)),
    isVideo ? 'video' : undefined,
  );

  if (isVideo && playable && playing) {
    if (!media.url) {
      /* 取流期间继续显示封面，不切黑块：黑块会让人以为又坏了。
         **取流失败要与取流中分开**：两者共用一个分支时，失败的画面会永远停在
         「正在读取视频…」，用户看不出是自己网络的问题还是素材坏了。 */
      return (
        <div className={cn('relative overflow-hidden bg-black', className)}>
          <CoverImage
            src={detail.data?.coverUrl ?? undefined}
            seed={assetId}
            alt={alt}
            className="h-full w-full opacity-60"
          />
          <span className="absolute inset-0 flex items-center justify-center text-micro text-white/80">
            {media.isPending ? '正在读取视频…' : '视频暂时读不出来'}
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

  if (isVideo && hoverPlay) {
    const playingNow = hoverNow && !!media.url;
    /* 外部接管时不再绑自己那对事件：整张卡已经负责了，两套状态各自 setHovered 只会打架
       （鼠标从封面滑到 chips 上会被判成「离开了」）。 */
    const ownEvents =
      active === undefined
        ? {
            onMouseEnter: () => setHovered(true),
            onMouseLeave: () => setHovered(false),
          }
        : {};
    return (
      <div className={cn('relative overflow-hidden', className)} {...ownEvents}>
        <CoverImage src={cover} seed={assetId} alt={alt} className="h-full w-full" />
        {/* 视频压在封面上而不是替换它：下载没完成时看到的仍是封面，不是黑块。 */}
        {playingNow && (
          <video
            src={media.url}
            muted
            loop
            autoPlay
            playsInline
            aria-hidden
            className="absolute inset-0 h-full w-full object-cover"
          />
        )}
        {!playingNow && (
          <span className="absolute bottom-1.5 right-1.5 rounded bg-black/55 px-1 py-0.5 text-micro text-white">
            视频
          </span>
        )}
      </div>
    );
  }

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
        <span className="absolute bottom-1.5 right-1.5 rounded bg-black/55 px-1 py-0.5 text-micro text-white">
          视频
        </span>
      )}
    </div>
  );
}
