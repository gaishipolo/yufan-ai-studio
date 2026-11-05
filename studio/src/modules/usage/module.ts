import { ChartColumn } from 'lucide-react';
import { defineModule } from '@/shared/registry/define-module';

/**
 * 用量看板：只声明路由与导航，页面当前是占位骨架。
 * 后端接口就绪后，改动只发生在 pages/usage + entities/usage，本文件无需变动。
 */
export default defineModule({
  id: 'usage',
  title: '用量看板',
  routes: [
    {
      path: 'usage',
      load: () => import('@/pages/usage/UsagePage'),
      // 用量是账号维度的计费信息，默认只对已登录成员开放。
      // 后端下发 `usage:read` 后打开下面一行即可启用门禁：
      // permissions: ['usage:read'],
    },
  ],
  nav: [
    {
      label: '用量看板',
      path: '/usage',
      icon: ChartColumn,
      group: '资产与数据',
      groupOrder: 3,
      order: 3,
    },
  ],
});
