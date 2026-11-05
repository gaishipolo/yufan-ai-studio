import { useRouteError } from 'react-router-dom';

/** 路由级兜底：lazy 页面加载失败或渲染抛错时接管整页 */
export function RouteError() {
  const error = useRouteError() as Error;
  return (
    <div className="grid min-h-screen place-items-center p-6 text-center">
      <div>
        <p className="text-[15px] font-semibold text-ink">页面加载失败</p>
        <p className="mt-2 max-w-md text-xs text-ink-mute">{error?.message ?? '未知错误'}</p>
        <button
          type="button"
          onClick={() => window.location.reload()}
          className="mt-4 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-white"
        >
          刷新重试
        </button>
      </div>
    </div>
  );
}
