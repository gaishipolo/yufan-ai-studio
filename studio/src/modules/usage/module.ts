import { ChartColumn } from 'lucide-react';
import { defineModule } from '@/shared/registry/define-module';

/**
 * 用量看板：只声明路由与导航，页面当前是占位骨架。
 * 后端接口就绪后，改动只发生在 pages/usage + entities/usage，本文件无需变动。
 *
 * **唯一没有声明 permissions 的模块**，原因具体：后端 `permission_catalog()` 里
 * 还没有 `usage:read` 这样的权限点，写上去就等于把页面永久锁死（fail-closed 且静默）。
 * 它是 `src/app/router.test.ts` 里 NO_PERMISSION_PATHS 的显式豁免项——
 * 后端下发权限点的同一次改动里，把这里补上、把豁免项删掉，那条测试会提醒你。
 */
export default defineModule({
  id: 'usage',
  title: '用量看板',
  routes: [
    {
      path: 'usage',
      load: () => import('@/pages/usage/UsagePage'),
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
