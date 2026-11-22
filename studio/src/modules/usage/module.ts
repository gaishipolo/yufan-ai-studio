import { ChartColumn } from 'lucide-react';
import { defineModule } from '@/shared/registry/define-module';

/**
 * 用量看板：按区间查看生成次数、已产出时长 / 张数、趋势与构成。
 *
 * 入口权限写页面码 `page:usage`（不是接口码 `usage:read`）：路由守卫只管
 * 「能不能进这一页」，接口码由后端逐个接口校验。两层分开，关掉页面时接口
 * 才不会漏着开着。
 *
 * `usage:read` / `usage:read:all` 的范围切换在页面里用 `useCan` 判，
 * 不写在这里 —— 那是**数据范围**（看自己 / 看全部成员），不是**页面入口**。
 */
export default defineModule({
  id: 'usage',
  title: '用量看板',
  routes: [
    {
      path: 'usage',
      load: () => import('@/pages/usage/UsagePage'),
      permissions: ['page:usage'],
      nav: {
        label: '用量看板',
        icon: ChartColumn,
        group: '资产与数据',
        groupOrder: 3,
        order: 3,
      },
    },
  ],
});
