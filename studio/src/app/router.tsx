import { Navigate, createBrowserRouter } from 'react-router-dom';
import { HOME_PATH } from '@/shared/config/app';
import { appRegistry } from '@/app/registry';
import { RouteError } from '@/app/RouteError';
import NotFoundPage from '@/pages/not-found/NotFoundPage';
import { RequirePermission } from '@/shared/access/RequirePermission';
import { AppShell } from '@/widgets/app-shell/AppShell';

/* ------------------------------------------------------------------
 * 路由表由注册表生成 —— 这个文件不需要随着新增功能而修改
 *
 * 新增页面 = 在 src/modules/<id>/module.ts 的 routes 里加一条。
 * 权限守卫也在这里统一包一层，业务侧不用手写。
 * ------------------------------------------------------------------ */

export const router = createBrowserRouter([
  {
    path: '/',
    element: <AppShell />,
    errorElement: <RouteError />,
    children: [
      { index: true, element: <Navigate to={HOME_PATH} replace /> },
      ...appRegistry.routes.map(({ path, Component, permissions, moduleId }) => ({
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
]);
