import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useLibraryLargePreview } from '@/entities/library/api/library.queries';

/**
 * 悬浮大图：鼠标停在缩略图上时，在旁边浮出能看清细节的那一张。
 *
 * ## 为什么需要它
 *
 * 网格里的缩略图用的是封面（后端 `create_preview` 缩到 360×240），
 * 在几十像素的格子里只剩色块 —— 产品图的走线、缝线、logo 全看不清。
 * 用户挑素材靠的是「看清再选」，所以另备一档长边 1280 的派生图。
 *
 * ## 三个不肯让步的点
 *
 * 1. **点一下仍然是选中**。浮层是 `pointer-events-none`：它绝不接管点击，
 *    鼠标事件穿透到下面的缩略图，用户不需要「先关掉浮层再点」。
 * 2. **浮层不挡路**。贴着缩略图显示，靠近视口右/下边缘时翻转方向，
 *    免得图被裁掉一半。
 * 3. **不许在鼠标快速划过时狂发请求**。悬停有延迟（`HOVER_DELAY_MS`），
 *    移开立即撤销 —— 划过一排格子不该产生十几个请求。
 */

/** 浮层宽度（px）。比网格格子的 3 倍略小，够看清又不至于盖满半个屏幕。 */
export const HOVER_PREVIEW_WIDTH = 320;
/**
 * 浮层最大高度。
 *
 * **这个数是按「竖图要能看全」定的**：产品三视图是竖长图（720×1280），
 * 320 宽按比例是 569px 高。上限低于它，竖图就只能看上面半截 ——
 * 而用户悬浮恰恰是为了看「背面长什么样」，裁掉等于没做这个功能。
 *
 * 600 够放下标准三视图（569px），同时占 900px 视口的 67%，网格还在旁边看得见。
 * 再高的图（极长竖图）会被限高并用 contain 完整显示（两侧留白，不裁）。
 */
export const HOVER_PREVIEW_MAX_HEIGHT = 600;
/** 鼠标停住多久才出图。快速划过不触发。 */
export const HOVER_PREVIEW_DELAY_MS = 220;
/** 浮层与鼠标的间距 */
const OFFSET = 16;

export interface AssetHoverPreviewProps {
  /** 悬停中的资产 id。`null` = 没有悬停任何一张（浮层不出现）。 */
  assetId: string | null;
  /** 素材名：浮层底部给一行标题，鼠标悬停时也能确认是哪一条 */
  name?: string;
  /**
   * 原图的宽高（像素）。用来算浮层该有多高。
   *
   * **必须传**：竖图（三视图 720×1280）与横图要占的高度差好几倍，
   * 一刀切限高就会把竖图裁掉一半 —— 而用户悬浮恰恰是为了看全图。
   * 列表出参本来就带 `width` / `height`，直接传，不用探尺寸。
   */
  width?: number | null;
  height?: number | null;
  /** 鼠标位置（视口坐标）—— 浮层跟着它走 */
  pointer: { x: number; y: number } | null;
  /** 浮层宽。默认 `HOVER_PREVIEW_WIDTH`。 */
  boxWidth?: number;
}

export function AssetHoverPreview({
  assetId,
  name,
  width: assetWidth,
  height: assetHeight,
  pointer,
  boxWidth = HOVER_PREVIEW_WIDTH,
}: AssetHoverPreviewProps) {
  const [armed, setArmed] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  /*
   * 计时器只认 `assetId`，**不认 pointer**。
   *
   * 鼠标一动 pointer 就变新对象，计时器跟着重置的话，图永远出不来
   * （用户得把鼠标钉在原地不动，等于不让用）。位置变化只影响浮层的
   * 样式，不该影响「要不要出图」。
   */
  useEffect(() => {
    if (!assetId) {
      setArmed(false);
      if (timer.current) {
        clearTimeout(timer.current);
        timer.current = undefined;
      }
      return undefined;
    }
    timer.current = setTimeout(() => setArmed(true), HOVER_PREVIEW_DELAY_MS);
    return () => {
      if (timer.current) {
        clearTimeout(timer.current);
        timer.current = undefined;
      }
    };
  }, [assetId]);

  /* 只有浮层真的要显示时才取字节：网格里几十张图，
     悬停一张才拉那一张 —— `enabled` 跟着 `armed` 走。 */
  const large = useLibraryLargePreview(assetId, armed);

  if (!assetId || !pointer || !armed || !large.url) return null;

  /*
   * 竖图不能一刀切 420px：产品三视图往往是竖长图（720×1280），
   * 限高 420 就只剩上面半截 —— 而用户悬浮恰恰是为了看「背面长什么样」，
   * 裁掉等于没做这个功能。
   *
   * 高度由**原图比例**算，不是由大图算：大图长边就是 1280，
   * 比例与原图一致，所以两者等价。
   */
  const ratio = assetWidth && assetHeight ? assetWidth / assetHeight : 1;
  /* 取整：亚像素高度会让浏览器按小数排版，边缘发虚。 */
  const naturalHeight = Math.round(boxWidth / ratio);
  /* 只有真的超高才限：限了仍然用 contain，图片完整显示（两侧留白），
     不会像 cover 那样把上下裁掉。 */
  const capped = naturalHeight > HOVER_PREVIEW_MAX_HEIGHT;
  const shownHeight = capped ? HOVER_PREVIEW_MAX_HEIGHT : naturalHeight;

  /* 贴边翻转：右边放不下就往左放，下边放不下就往上放。
     算的是**浮层的右/下边缘**能不能进视口，不是中心点。
     下边用实际显示高度（`shownHeight`），不是最大高度 —— 否则矮图
     会被判定成「放不下」，无缘无故往上翻。 */
  const viewportWidth = typeof window === 'undefined' ? 1024 : window.innerWidth;
  const viewportHeight = typeof window === 'undefined' ? 768 : window.innerHeight;
  const flipLeft = pointer.x + OFFSET + boxWidth > viewportWidth - 8;
  const flipUp = pointer.y + OFFSET + shownHeight > viewportHeight - 8;
  let left = flipLeft ? pointer.x - OFFSET - boxWidth : pointer.x + OFFSET;
  let top = flipUp ? pointer.y - OFFSET - shownHeight : pointer.y + OFFSET;

  /* 上下都放不下时（竖图比视口还高、鼠标又在中间）：
     贴到视口顶边，**不要**让它跑到视口外面去 ——
     负坐标的浮层上半截看不见，用户看到的是「图少了一截」，
     比「图小一点」更难解释。 */
  if (top < 8) top = 8;
  if (top + shownHeight > viewportHeight - 8) {
    top = Math.max(8, viewportHeight - 8 - shownHeight);
  }
  /* 左右同理：贴边而不是出屏 */
  if (left < 8) left = 8;
  if (left + boxWidth > viewportWidth - 8) {
    left = Math.max(8, viewportWidth - 8 - boxWidth);
  }

  return createPortal(
    <div
      /* `pointer-events-none`：浮层绝不接管点击，点一下仍然是选中。 */
      className="pointer-events-none fixed z-[150] rounded-xl border border-line-soft bg-panel shadow-float animate-fadeIn"
      style={{ left, top, width: boxWidth }}
      aria-hidden
    >
      <img
        src={large.url}
        alt={name ?? ''}
        className="block w-full rounded-t-xl bg-white"
        style={{ height: shownHeight, objectFit: capped ? 'contain' : 'cover' }}
      />
      {name && (
        <div className="truncate border-t border-line-soft px-2.5 py-1.5 text-micro text-ink-soft">
          {name}
        </div>
      )}
    </div>,
    document.body,
  );
}
