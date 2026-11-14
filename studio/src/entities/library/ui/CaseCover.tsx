import { ImageOff } from 'lucide-react';
import { cn } from '@/shared/lib/cn';
import { AssetPreview } from './AssetPreview';
import { CoverImage } from '@/shared/ui';

/**
 * 展示案例封面：有案例给图（视频悬停自动播），没有就是兜底渐变 + 「暂无案例」。
 *
 * 模板库的卡片和封装列表的卡片必须长得一样 —— 同一张模板在两个页面上是两种
 * 封面，用户会以为其中一边没加载出来。空态也不摆虚线框：那圈虚线比图还显眼，
 * 还会被读成"加载失败"。
 */
export function CaseCover({
  assetId,
  seed,
  className,
  hoverPlay,
}: {
  assetId: string | null | undefined;
  /** 兜底渐变的种子，传模板 key：同一张模板每次都同一个色，不会闪 */
  seed: string;
  className?: string;
  /** 视频卡片：鼠标移上去自动静音循环，取流只在 hover 时发生 */
  hoverPlay?: boolean;
}) {
  if (!assetId) {
    return (
      <div className={cn('relative overflow-hidden', className)}>
        <CoverImage seed={seed || 'template'} className="h-full w-full" />
        <span className="absolute inset-0 flex flex-col items-center justify-center gap-1.5 text-white/75">
          <span className="grid size-7 place-items-center rounded-full bg-white/15 ring-1 ring-inset ring-white/20">
            <ImageOff size={13} />
          </span>
          <span className="text-[10px] tracking-wide">暂无案例</span>
        </span>
      </div>
    );
  }
  return (
    <AssetPreview
      assetId={assetId}
      className={className}
      {...(hoverPlay ? {hoverPlay: true} : {})}
    />
  );
}
