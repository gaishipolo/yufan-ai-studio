import { Suspense } from 'react';
import { Navigate, createBrowserRouter } from 'react-router-dom';
import { HOME_PATH } from '@/shared/config/app';
import { appRegistry } from '@/app/registry';
import { RouteError } from '@/app/RouteError';
import NotFoundPage from '@/pages/not-found/NotFoundPage';
import { RequireAuth } from '@/shared/access/RequireAuth';
import { RequirePermission } from '@/shared/access/RequirePermission';
import { FullPageLoader } from '@/shared/ui/FullPageLoader';
import { AppShell } from '@/widgets/app-shell/AppShell';

/* ------------------------------------------------------------------
 * 路由表由注册表生成 —— 这个文件不需要随着新增功能而修改
 *
 * 新增页面 = 在 src/modules/<id>/module.ts 的 routes 里加一条：
 *   layout: 'shell'（默认）→ 套外壳，受登录守卫 + 权限守卫保护
 *   layout: 'bare'        → 独立整页（登录页这类），不套外壳、不做登录守卫
 * 守卫在这里统一包，业务侧不用手写。
 * ------------------------------------------------------------------ */

const shellRoutes = appRegistry.routes.filter((route) => route.layout === 'shell');
const bareRoutes = appRegistry.routes.filter((route) => route.layout === 'bare');

export const router = createBrowserRouter([
  {
    path: '/',
    element: (
      <RequireAuth>
        <AppShell />
      </RequireAuth>
    ),
    errorElement: <RouteError />,
    children: [
      { index: true, element: <Navigate to={HOME_PATH} replace /> },
      ...shellRoutes.map(({ path, Component, permissions, moduleId }) => ({
        path: path.replace(/^\//, ''),
        handle: { moduleId },
        element: (
          <RequirePermission permissions={permissions}>
            <Component />
          </RequirePermission>
        ),
      })),
      { path: '*', element: <NotFoundPage /> },
    ],
  },
  /* 独立布局页面：作为顶层路由，与外壳平级 */
  ...bareRoutes.map(({ path, Component, moduleId }) => ({
    path,
    handle: { moduleId },
    errorElement: <RouteError />,
    element: (
      <Suspense fallback={<FullPageLoader />}>
        <Component />
      </Suspense>
    ),
  })),
]);
