import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Layers } from 'lucide-react';
import { useLibraryPack } from '../api/library.queries';
import { CoverImage } from '@/shared/ui';
import { displayName } from '../model/types';

/**
 * 悬浮预览：鼠标停在**封装包**的格子上时，展开包里装了什么。
 *
 * ## 为什么包要单独一个浮层
 *
 * 素材的悬停是「看清这一张的细节」（`AssetHoverPreview` 取长边 1280 的大图）。
 * 包没有自己的字节，它的封面只是第一个成员的封面 —— 拿那张图放大，
 * 用户看到的是「一张普通产品图」，完全不知道这个包里还有正面 / 三视图 / 细节。
 *
 * 所以包要预览的是**成员列表本身**：每一格一张缩略图，一眼能数出「这套齐不齐」。
 *
 * ## 三条与 `AssetHoverPreview` 一致的口径
 *
 * 1. **点一下仍然是选中**：浮层 `pointer-events-none`，绝不接管点击；
 * 2. **不许划过就狂发请求**：悬停有延迟，移开立即撤销，`enabled` 跟着延迟走；
 * 3. **贴边翻转**：右边 / 下边放不下就翻到另一侧，且不出视口。
 */

/** 浮层宽度：够摆 3 列缩略图，又不至于盖住半个屏幕 */
const BOX_WIDTH = 300;
/** 鼠标停住多久才出浮层（与素材预览一致） */
const DELAY_MS = 220;
const OFFSET = 16;
/** 最多显示几张成员：多了就换行堆高，浮层会盖满屏幕 */
const MAX_MEMBERS = 6;

export interface PackHoverPreviewProps {
  /** 悬停中的封装 id。`null` = 没有悬停任何包 */
  packId: string | null;
  /** 鼠标位置（视口坐标） */
  pointer: { x: number; y: number } | null;
}

export function PackHoverPreview({ packId, pointer }: PackHoverPreviewProps) {
  const [armed, setArmed] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  /* 计时器只认 `packId`，不认 pointer：鼠标一动 pointer 就是新对象，
     跟着重置的话浮层永远出不来（用户得把鼠标钉死不动）。 */
  useEffect(() => {
    if (!packId) {
      setArmed(false);
      if (timer.current) {
        clearTimeout(timer.current);
        timer.current = undefined;
      }
      return undefined;
    }
    timer.current = setTimeout(() => setArmed(true), DELAY_MS);
    return () => {
      if (timer.current) {
        clearTimeout(timer.current);
        timer.current = undefined;
      }
    };
  }, [packId]);

  const pack = useLibraryPack(armed ? packId : null);

  if (!packId || !pointer || !armed || !pack.data) return null;

  const members = pack.data.members ?? [];
  const shown = members.slice(0, MAX_MEMBERS);
  const rest = members.length - shown.length;

  const viewportWidth = typeof window === 'undefined' ? 1024 : window.innerWidth;
  const viewportHeight = typeof window === 'undefined' ? 768 : window.innerHeight;
  const flipLeft = pointer.x + OFFSET + BOX_WIDTH > viewportWidth - 8;
  const flipUp = pointer.y + OFFSET + 260 > viewportHeight - 8;
  let left = flipLeft ? pointer.x - OFFSET - BOX_WIDTH : pointer.x + OFFSET;
  let top = flipUp ? pointer.y - OFFSET - 260 : pointer.y + OFFSET;
  if (left < 8) left = 8;
  if (left + BOX_WIDTH > viewportWidth - 8) left = Math.max(8, viewportWidth - 8 - BOX_WIDTH);
  if (top < 8) top = 8;

  return createPortal(
    <div
      className="pointer-events-none fixed z-[150] rounded-xl border border-line-soft bg-panel p-2 shadow-float animate-fadeIn"
      style={{ left, top, width: BOX_WIDTH }}
      aria-hidden
    >
      <div className="mb-1.5 flex items-center gap-1.5 px-0.5">
        <Layers size={12} className="shrink-0 text-ink-mute" />
        <span className="min-w-0 truncate text-micro font-medium text-ink" title={pack.data.name}>
          {displayName(pack.data.name)}
        </span>
        <span className="ml-auto shrink-0 text-micro text-ink-mute">{members.length} 张</span>
      </div>
      <div className="grid grid-cols-3 gap-1">
        {shown.map((member) => (
          <div
            key={member.id}
            className="relative overflow-hidden rounded-md border border-line-soft"
            title={member.name}
          >
            <CoverImage src={member.coverUrl ?? undefined} seed={member.id} className="aspect-square" />
            {/* 回收站里的成员仍然显示（并标出来）：包里少一张却不说明，
                用户会以为这个包本来就只有这么多。 */}
            {member.deleted && (
              <span className="absolute inset-x-0 bottom-0 bg-[rgba(12,24,30,.66)] py-px text-center text-[9px] text-white">
                已删除
              </span>
            )}
          </div>
        ))}
      </div>
      {rest > 0 && (
        <div className="mt-1 text-center text-micro text-ink-mute">还有 {rest} 张</div>
      )}
    </div>,
    document.body,
  );
}
