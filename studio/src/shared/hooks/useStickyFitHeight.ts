import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * 吸顶面板下沿离视口底边留的余量。
 *
 * 取 12 是为了跟面板上沿的间距对称：吸顶位置 71、顶栏（含胶片边）下沿 59，
 * 中间正好 12。上下留一样宽，面板才不像「贴着底边被切了一半」。
 */
export const STICKY_FIT_MARGIN = 12;

/**
 * 一个吸顶面板最多能有多高：**从它本来的位置到视口底边，减去余量。**
 *
 * 拆成纯函数是为了能单测 —— 这个数算错一点点，面板就会被视口切掉一条边，
 * 而「被切掉」在屏幕上只是「看着有点怪」，当场很难归因到某个数字上。
 */
export function fitHeightToViewport(
  viewportHeight: number,
  naturalTop: number,
  margin: number = STICKY_FIT_MARGIN,
): number {
  return Math.max(0, Math.round(viewportHeight - naturalTop - margin));
}

export interface StickyFit {
  /** 挂到面板上：面板一挂上来就量一次 */
  ref: (node: HTMLElement | null) => void;
  /** 高度上限（px）。普通流里（不吸顶）不设限，返回 undefined */
  maxHeight: number | undefined;
}

/**
 * 吸顶面板的高度上限：**量它站在哪儿，不写死「视口减几」。**
 *
 * ## 为什么
 *
 * 模板库的右栏（挑素材 → 生成）是个吸顶面板：内容比视口高，中间自己滚，
 * 头（模板名）和脚（生成按钮）留在外面。以前它的上限写死成 `100vh - 91px`
 * —— 那个 91 是**吸住之后**的位置算出来的（71 + 20）。
 *
 * 可页面刚打开时它还没吸住：上面还顶着「模板库」这行标题，
 * 面板的自然位置在 110，比吸顶位置低 39px。底边因此落在 110 + 760 = 871，
 * 超出 860 的视口 11px —— 用户看到的正是「滚动到顶部时右栏被遮住一点，
 * 页面往下滚一点才完整」。
 *
 * 写死的上限只对「吸住之后」成立，对「还没吸住」的那一刻不成立，
 * 而那恰恰是用户第一眼看到的样子。
 *
 * ## 怎么量
 *
 * 吸住以后 `getBoundingClientRect()` 给的是**被钉住后的位置**，
 * 不是它在文档流里本来的位置 —— 直接拿它算上限会把上限算大一大截。
 * 量「本来的位置」只有一个办法：临时把 `sticky` 摘掉读一次，再装回去。
 *
 * ## 什么时候量
 *
 * 面板挂上来的时候量 —— 不是组件挂载的时候量。
 * 模板库右栏是等数据回来才挂的，只在挂载时量的话量到的还是空气，
 * 上限就一直是「不限高」，bug 原样复现（这个坑踩过一次）。
 *
 * ## 为什么上限是恒定的
 *
 * 上限按「自然位置」算一次就不变了：面板不会随着滚动忽胖忽瘦。
 * 代价是吸住之后下方会多留一点空（自然位置比吸顶位置低的那截），
 * 换来的是面板不会在滚动中改变尺寸 —— 一个会呼吸的面板比一个矮一截的面板更扎眼。
 */
export function useStickyFitHeight(margin: number = STICKY_FIT_MARGIN): StickyFit {
  const [maxHeight, setMaxHeight] = useState<number | undefined>(undefined);
  const elementRef = useRef<HTMLElement | null>(null);

  const measure = useCallback(
    (element: HTMLElement | null) => {
      if (!element) return;

      // 普通流里不需要上限：面板该多高就多高，页面自己会滚。
      if (getComputedStyle(element).position !== 'sticky') {
        setMaxHeight(undefined);
        return;
      }

      const inline = element.style.position;
      element.style.position = 'static';
      const naturalTop = element.getBoundingClientRect().top + window.scrollY;
      element.style.position = inline;

      setMaxHeight(previous => {
        const next = fitHeightToViewport(window.innerHeight, naturalTop, margin);
        return previous === next ? previous : next;
      });
    },
    [margin],
  );

  /* 回调 ref 而不是 useRef + effect：面板可能晚于组件挂载才出现
     （数据没回来之前右栏根本不渲染），那时候得再量一次。 */
  const ref = useCallback(
    (node: HTMLElement | null) => {
      elementRef.current = node;
      if (node) measure(node);
    },
    [measure],
  );

  useEffect(() => {
    // 只有窗口尺寸变了才需要重量：自然位置由上方内容决定，不随滚动变。
    const onResize = () => measure(elementRef.current);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [measure]);

  return {ref, maxHeight};
}
