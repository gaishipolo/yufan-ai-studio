import { Wrench } from 'lucide-react';
import { defineModule } from '@/shared/registry/define-module';

/**
 * 运维页（P4）：单独一页承载「刷新驱动目录」。
 *
 * 页面码 `page:ops` 与接口码 `model_driver:refresh` 分开：路由守卫只管
 * 「能不能进这一页」，接口码由后端逐个接口校验。两层分开，关掉页面时
 * 接口才不会漏着开着。
 *
 * 审计（刷新历史）后端目前只有日志、没有可查询接口，页面不提供历史列表。
 */
export default defineModule({
  id: 'ops',
  title: '运维',
  routes: [
    {
      path: 'ops',
      load: () => import('@/pages/ops/OpsPage'),
      permissions: ['page:ops'],
      nav: {
        label: '运维',
        icon: Wrench,
        group: '运维',
        groupOrder: 10,
        order: 1,
      },
    },
  ],
});
