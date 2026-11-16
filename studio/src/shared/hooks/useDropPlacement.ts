import { useCallback, useLayoutEffect, useState, type RefObject } from 'react';

/** 浮层与锚点之间留的缝 */
const GAP = 4;

export interface DropPlacement {
  /** 往哪边展开：`bottom` 贴着锚点下方，`top` 贴着锚点上方 */
  side: 'top' | 'bottom';
  /** 那一侧还剩多少像素（已扣掉间隙）—— 用来给浮层封顶，别让它溢出视口 */
  space: number;
}

/**
 * 浮层往哪边展开：**量出来，不靠调用方声明**。
 *
 * ## 为什么
 *
 * 浮层写死「往下弹」有个硬伤：页面底部没有空间时，它整块落到屏幕外 ——
 * 用户看到的是「输入框闪了一下、什么也没出现」（模板封装的 @ 候选面板
 * 就撞过这个：提示词框在右栏最下面，候选条永远朝下，直接被视口底边切掉）。
 *
 * 以前 `MultiSelect` 自己量过一次，这次把那段逻辑提出来共用 ——
 * 两份实现一旦漂移，就是「同一个浮层在两个页面表现不一样」。
 *
 * ## 量的是什么
 *
 * - `anchorRef`：浮层挂靠的那个元素（触发器 / 输入框）。它的下沿和视口下沿
 *   之间还有多少空间，决定了朝下弹不弹得下。
 * - `popupRef`：浮层本身。**量它自己的高度**比猜一个常数准：候选多的时候高、
 *   候选少的时候矮，用固定阈值会把「其实放得下」也判成放不下。
 * - `minSpace`：调用方声明的**下限**（比如「这个浮层至少要 250px 才算展开得开」）。
 *   两者取大的那个 —— 实测高度覆盖默认值，声明值兜底。
 *
 * ## 什么时候量
 *
 * 打开时量一次；开着的时候页面一滚就重算 —— 锚点相对视口的位置变了，
 * 方向不跟着变，浮层就会悬在半空或者跑出屏幕。用 `useLayoutEffect`：
 * 测量完立刻改方向，浏览器还没来得及画那一帧，用户看不到「先弹错方向再跳过去」。
 */
export function useDropPlacement(
  anchorRef: RefObject<HTMLElement | null>,
  popupRef: RefObject<HTMLElement | null>,
  open: boolean,
  minSpace = 0,
): DropPlacement {
  const [placement, setPlacement] = useState<DropPlacement>({
    side: 'bottom',
    space: Number.POSITIVE_INFINITY,
  });

  const measure = useCallback(() => {
    const anchor = anchorRef.current;
    if (!anchor) return;
    const rect = anchor.getBoundingClientRect();
    const popupHeight = popupRef.current?.getBoundingClientRect().height ?? 0;
    const need = Math.max(minSpace, popupHeight) + GAP;

    const below = window.innerHeight - rect.bottom - GAP;
    const above = rect.top - GAP;

    let side: 'top' | 'bottom';
    if (below >= need) side = 'bottom';
    else if (above >= need) side = 'top';
    // 两边都不够：选宽的那边，至少露得多一点
    else side = above > below ? 'top' : 'bottom';

    const space = Math.max(0, side === 'bottom' ? below : above);
    setPlacement(previous =>
      // 滚动时别每帧都改 state：方向没变、空间没差多少就别动
      previous.side === side && Math.abs(previous.space - space) < 8
        ? previous
        : { side, space },
    );
  }, [anchorRef, popupRef, minSpace]);

  useLayoutEffect(() => {
    if (!open) return;
    measure();
    // scroll 不冒泡但会走捕获阶段，用 capture 才能抓到所有滚动容器
    window.addEventListener('scroll', measure, true);
    window.addEventListener('resize', measure);
    return () => {
      window.removeEventListener('scroll', measure, true);
      window.removeEventListener('resize', measure);
    };
  }, [open, measure]);

  return placement;
}
