import { Suspense } from 'react';
import { Outlet } from 'react-router-dom';
import { Slot } from '@/shared/registry/Slot';
import { CardSkeletonGrid, Skeleton, Toaster } from '@/shared/ui';
import { Sidebar } from '@/widgets/sidebar/Sidebar';
import { TopBar } from '@/widgets/top-bar/TopBar';

function PageFallback() {
  return (
    <div className="p-1">
      <Skeleton className="mb-4 h-5 w-32" />
      <CardSkeletonGrid count={8} />
    </div>
  );
}

/**
 * 应用外壳：只负责布局与插槽，不认识任何业务模块。
 */
export function AppShell() {
  return (
    <div className="min-h-screen">
      <TopBar />
      {/* 顶栏下那排模块宫格撤了（2026-11-16）：侧栏已经是一组完整的入口，
          宫格是同一批入口的第二份画法 —— 一屏两套导航，用户的注意力被摊薄，
          内容区还被往下挤掉一整块。 */}
      <div className="grid grid-cols-1 items-start gap-3.5 px-3.5 pb-[18px] pt-3 md:grid-cols-[196px_minmax(0,1fr)] md:px-[22px] md:pb-[22px] xl:grid-cols-[208px_minmax(0,1fr)] 2xl:px-7">
        <Sidebar />
        <main className="min-h-[calc(100vh-190px)] min-w-0 rounded-panel border border-[rgba(222,234,238,.9)] bg-panel px-3 py-3.5 pb-[18px] shadow-card md:px-5 md:pb-6 md:pt-[18px] 2xl:px-6">
          <Suspense fallback={<PageFallback />}>
            <Outlet />
          </Suspense>
        </main>
      </div>
      {/* 全局浮层插槽：命令面板、全局搜索等 */}
      <Slot name="shell.overlay" />
      <Toaster />
    </div>
  );
}
