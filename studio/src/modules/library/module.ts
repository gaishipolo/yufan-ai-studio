import { Library } from 'lucide-react';
import { defineModule } from '@/shared/registry/define-module';

/**
 * 资产库 v2（/library）—— 与现役「素材资产库」（/assets）并行的第二版。
 *
 * M5 起它是**主入口**：侧栏与首页宫格里排在旧库前面。旧库不删、不藏，
 * 两个入口同时可用——并存观察期需要「能随时退回去对比」，藏起来等于
 * 把回退路径变成「改一行代码再重启」。
 *
 * 权限用新前缀 `library:*`，与旧 `asset:*` 完全隔离：给旧库配的权限
 * 不会顺带打开 v2 的可见范围。所以只配了 `asset:read` 的账号看得到旧入口、
 * 看不到这个，这是预期的，不是漏配。
 */
export default defineModule({
  id: 'library',
  title: '资产库',
  routes: [
    {
      path: 'library',
      load: () => import('@/pages/library/LibraryPage'),
      permissions: ['library:read'],
      nav: {
        label: '资产库',
        icon: Library,
        group: '资产与数据',
        groupOrder: 3,
        order: 1,
      },
      card: {
        subtitle: '草稿 / 小组 / 团队',
        gradient: 'linear-gradient(140deg,#3ddc97,#12b3a0)',
        order: 3,
      },
    },
  ],
});
