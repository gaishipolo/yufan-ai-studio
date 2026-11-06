import { ShieldCheck } from 'lucide-react';
import { defineModule } from '@/shared/registry/define-module';

/**
 * 用户与权限。
 *
 * 权限只在 route 上写一行 `identity:read`，三处自动生效：路由守卫（手输 URL 也 403）、
 * 侧边栏入口显隐（由 route.nav 派生，权限自动继承），以及页面内按钮的 `<Can>`
 * （见 UsersSection / RolesSection）。权限点由后端下发，前端不硬编码。
 */
export default defineModule({
  id: 'user-access',
  title: '用户与权限',
  routes: [
    {
      path: 'system/user-access',
      load: () => import('@/pages/user-access/UserAccessPage'),
      permissions: ['identity:read'],
      nav: {
        label: '用户与权限',
        icon: ShieldCheck,
        group: '系统管理',
        groupOrder: 9,
      },
    },
  ],
});
