/**
 * 品牌图形标（「YF」组合字）。
 *
 * 直接引用 `public/favicon.svg`，让页签图标和界面里的字标共用同一份文件：
 * 换 logo 只改一个文件，不会出现「页签换了、顶栏还是旧图」。
 * 不内联路径是为了不让 6KB 的 path 数据进 JS 包（它属于静态资源，可以单独缓存）。
 *
 * 尺寸只给高度、宽度自动，保持图标原本的 164:145 比例——硬拉成正方形会压扁字形。
 */
export function BrandMark({ size = 24, className }: { size?: number; className?: string }) {
  return (
    <img
      src="/favicon.svg"
      alt=""
      aria-hidden
      // 装饰性图形：品牌名由相邻文字给出，读屏不必再念一遍
      className={['block shrink-0 select-none', className].filter(Boolean).join(' ')}
      style={{ height: size, width: 'auto' }}
    />
  );
}
