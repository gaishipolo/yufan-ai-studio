import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AssetHoverPreview } from './AssetHoverPreview';

/**
 * 悬浮大图的三条硬约束：
 *
 * 1. **点一下仍然是选中** —— 浮层绝不接管点击（`pointer-events-none`）；
 * 2. **不许在鼠标快速划过时狂发请求** —— 悬停有延迟；
 * 3. **浮层不挡路** —— 贴边时翻转方向。
 *
 * 用 `getByAltText` 而不是 `getByRole('img')`：浮层整体是 `aria-hidden`
 * （它是缩略图内容的重复，念出来只会吵），role 查询遵守无障碍树、
 * 找不到 `aria-hidden` 里的元素。
 */

const { largeCalls } = vi.hoisted(() => ({
  largeCalls: [] as string[],
}));

vi.mock('@/entities/library/api/library.queries', () => ({
  useLibraryLargePreview: (assetId: string | null, enabled: boolean) => {
    /* 只有 enabled 时才算「发了请求」：这是「快速划过不发请求」那条守卫的探针。 */
    if (enabled && assetId) largeCalls.push(assetId);
    return {
      url: enabled && assetId ? `blob:${assetId}` : undefined,
      isPending: false,
    };
  },
}));

beforeEach(() => {
  largeCalls.length = 0;
});

/** 浮层挂在 body 上（portal），所以要从 document 里捞，不能只看 container。 */
function layer(): HTMLElement | null {
  return document.querySelector('.pointer-events-none');
}

function renderPreview(
  props: Partial<React.ComponentProps<typeof AssetHoverPreview>> = {},
) {
  return render(
    <AssetHoverPreview
      assetId="a1"
      name="T35黑色三视图"
      pointer={{ x: 100, y: 100 }}
      {...props}
    />,
  );
}

describe('AssetHoverPreview · 悬浮大图', () => {
  it('悬停前不出图', () => {
    vi.useFakeTimers();
    try {
      renderPreview();
      expect(screen.queryByAltText('T35黑色三视图')).not.toBeInTheDocument();
      expect(largeCalls).toEqual([]);
    } finally {
      vi.useRealTimers();
    }
  });

  it('悬停够久就浮出大图', async () => {
    vi.useFakeTimers();
    try {
      renderPreview();
      await vi.advanceTimersByTimeAsync(300);
      expect(screen.getByAltText('T35黑色三视图')).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it('浮层不接管点击（点缩略图仍然是选中）', async () => {
    vi.useFakeTimers();
    try {
      renderPreview();
      await vi.advanceTimersByTimeAsync(300);
      const el = layer();
      expect(el).not.toBeNull();
      /* 这一条是「点一下仍然是选择」的技术保证：浮层全程 pointer-events-none，
         鼠标事件穿透到下面的缩略图。 */
      expect(el?.className).toContain('pointer-events-none');
    } finally {
      vi.useRealTimers();
    }
  });

  it('鼠标移开就撤掉浮层', async () => {
    vi.useFakeTimers();
    try {
      const { rerender } = renderPreview();
      await vi.advanceTimersByTimeAsync(300);
      expect(screen.getByAltText('T35黑色三视图')).toBeInTheDocument();
      /* 移开 = assetId 变 null */
      rerender(
        <AssetHoverPreview assetId={null} name="T35黑色三视图" pointer={null} />,
      );
      expect(screen.queryByAltText('T35黑色三视图')).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it('没悬停任何一条时不发请求', () => {
    renderPreview({ assetId: null, pointer: null });
    expect(largeCalls).toEqual([]);
  });

  it('悬停才发请求，且发的是那条资产', async () => {
    vi.useFakeTimers();
    try {
      renderPreview({ assetId: 'a7' });
      await vi.advanceTimersByTimeAsync(300);
      expect(largeCalls).toContain('a7');
    } finally {
      vi.useRealTimers();
    }
  });

  it('鼠标移动只改位置，不会重置计时器（否则图永远出不来）', async () => {
    vi.useFakeTimers();
    try {
      const { rerender } = renderPreview({ pointer: { x: 100, y: 100 } });
      /* 鼠标移动 → pointer 变新对象 */
      rerender(
        <AssetHoverPreview
          assetId="a1"
          name="T35黑色三视图"
          pointer={{ x: 130, y: 140 }}
        />,
      );
      /* 计时器只认 assetId：这时图就该出来了 */
      await vi.advanceTimersByTimeAsync(300);
      expect(screen.getByAltText('T35黑色三视图')).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it('**连续**移动鼠标也照样出图（计时器不能被每次移动重置）', async () => {
    /* 这条是上面那条的加强版：上面只移动一次，坏实现也能蒙混过关
       （重置后的计时器仍然会到期）。
       真实场景是鼠标一直动 —— 每动一次就重置一次，图就永远出不来。
       所以这里要在延迟窗口内**反复**移动，每次都只推进一点点时间。 */
    vi.useFakeTimers();
    try {
      const { rerender } = renderPreview({ pointer: { x: 100, y: 100 } });
      for (let step = 0; step < 10; step += 1) {
        rerender(
          <AssetHoverPreview
            assetId="a1"
            name="T35黑色三视图"
            pointer={{ x: 100 + step, y: 100 + step }}
          />,
        );
        /* 每次只推进 50ms：总共 500ms > 220ms 的延迟，
           但任何一次移动都没有连续待够 220ms。 */
        await vi.advanceTimersByTimeAsync(50);
      }
      expect(screen.getByAltText('T35黑色三视图')).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});

/* 竖图（三视图 720×1280）是这个功能的主要场景：
   一刀切限高 420 就只剩上面半截 —— 而用户悬浮恰恰是为了看「背面长什么样」。 */
describe('AssetHoverPreview · 竖图不被裁', () => {
  it('竖图的高度按原图比例算，不是固定 420', async () => {
    vi.useFakeTimers();
    try {
      renderPreview({ width: 720, height: 1280 });
      await vi.advanceTimersByTimeAsync(300);
      const img = document.querySelector('img[alt="T35黑色三视图"]') as HTMLImageElement;
      expect(img).not.toBeNull();
      /* 320 宽 × (1280/720) = 569px 高 */
      expect(img.style.height).toBe('569px');
    } finally {
      vi.useRealTimers();
    }
  });

  it('极高的图限高，但用 contain 完整显示（不裁掉上下）', async () => {
    vi.useFakeTimers();
    try {
      /* 320 × (4000/500) = 2560px，远超上限 */
      renderPreview({ width: 500, height: 4000 });
      await vi.advanceTimersByTimeAsync(300);
      const img = document.querySelector('img[alt="T35黑色三视图"]') as HTMLImageElement;
      expect(img.style.height).toBe('600px');
      /* contain = 完整显示（两侧留白）；cover 会把上下裁掉 */
      expect(img.style.objectFit).toBe('contain');
    } finally {
      vi.useRealTimers();
    }
  });

  it('横图的高度也按比例算', async () => {
    vi.useFakeTimers();
    try {
      renderPreview({ width: 2752, height: 1536 });
      await vi.advanceTimersByTimeAsync(300);
      const img = document.querySelector('img[alt="T35黑色三视图"]') as HTMLImageElement;
      /* 320 × (1536/2752) = 178.6 → 取整 179px */
      expect(img.style.height).toBe('179px');
      expect(img.style.objectFit).toBe('cover');
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('AssetHoverPreview · 贴边翻转', () => {
  it('右边放不下就往左放', async () => {
    vi.useFakeTimers();
    try {
      renderPreview({ pointer: { x: window.innerWidth - 10, y: 100 } });
      await vi.advanceTimersByTimeAsync(300);
      const left = Number.parseFloat(layer()?.style.left ?? '');
      /* 往左放 = 左边缘在鼠标左边，而不是右边出屏 */
      expect(left).toBeLessThan(window.innerWidth - 10);
    } finally {
      vi.useRealTimers();
    }
  });

  it('下边放不下就往上放', async () => {
    vi.useFakeTimers();
    try {
      renderPreview({ pointer: { x: 100, y: window.innerHeight - 10 } });
      await vi.advanceTimersByTimeAsync(300);
      const top = Number.parseFloat(layer()?.style.top ?? '');
      expect(top).toBeLessThan(window.innerHeight - 10);
    } finally {
      vi.useRealTimers();
    }
  });

  it('上下都放不下时贴视口顶边，不跑到视口外（负坐标 = 上半截看不见）', async () => {
    vi.useFakeTimers();
    try {
      /* 竖图 720×1280 → 569px 高，视口 768。
         鼠标在 y=300：往下放不下（300+16+569=885 > 760），
         往上翻又是 300-16-569 = -285（出屏）—— 这才是真正的两难。 */
      renderPreview({
        width: 720,
        height: 1280,
        pointer: { x: 100, y: 300 },
      });
      await vi.advanceTimersByTimeAsync(300);
      const top = Number.parseFloat(layer()?.style.top ?? '');
      expect(top).toBeGreaterThanOrEqual(0);
      expect(top).toBeLessThanOrEqual(8);
    } finally {
      vi.useRealTimers();
    }
  });

  it('左右也不出屏', async () => {
    vi.useFakeTimers();
    try {
      renderPreview({ pointer: { x: 5, y: 100 } });
      await vi.advanceTimersByTimeAsync(300);
      const left = Number.parseFloat(layer()?.style.left ?? '');
      expect(left).toBeGreaterThanOrEqual(0);
    } finally {
      vi.useRealTimers();
    }
  });
});

/* 这条是给「点一下仍然是选中」留的端到端证据：真实点击缩略图按钮，
   浮层出现后按钮依然能点、依然会触发选择。 */
describe('AssetHoverPreview · 与选择行为共存', () => {
  it('浮层出现后点缩略图仍然触发选择', async () => {
    vi.useFakeTimers();
    try {
      const onClick = vi.fn();
      render(
        <button type="button" onClick={onClick} title="T35黑色三视图">
          <AssetHoverPreview
            assetId="a1"
            name="T35黑色三视图"
            pointer={{ x: 100, y: 100 }}
          />
        </button>,
      );
      await vi.advanceTimersByTimeAsync(300);
      expect(screen.getByAltText('T35黑色三视图')).toBeInTheDocument();
      /* 用 fireEvent 而不是 userEvent：后者在 fake timers 下要靠真实时钟推进，
         会和手动推进的计时器打架（挂住直到超时）。 */
      fireEvent.click(screen.getByRole('button', { name: 'T35黑色三视图' }));
      expect(onClick).toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });
});
