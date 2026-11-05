import { defineModule } from '@/shared/registry/define-module';
import { LOGIN_PATH } from '@/shared/config/app';
import { UserMenuItems } from '@/features/auth/UserMenuItems';

/**
 * 登录与账号。
 *
 * 这个模块只做两件事：
 * 1. 声明登录页路由（`layout: 'bare'`：不套外壳，也不受登录守卫约束，
 *    否则未登录访问 /login 会被守卫再送回 /login 形成死循环）；
 * 2. 往顶栏账号菜单里注入「个人资料 / 修改密码」。
 *
 * 会话本身（令牌、身份、401 处理）属于骨架，见 shared/access/session.ts。
 */
export default defineModule({
  id: 'auth',
  title: '登录与账号',
  routes: [
    {
      path: LOGIN_PATH,
      layout: 'bare',
      load: () => import('@/pages/login/LoginPage'),
    },
  ],
  slots: [{ name: 'user-menu.items', component: UserMenuItems, order: 10 }],
});
