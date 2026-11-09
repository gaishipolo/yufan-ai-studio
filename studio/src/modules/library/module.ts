import { Library } from 'lucide-react';
import { defineModule } from '@/shared/registry/define-module';

/**
 * 资产库 v2（/library）—— 与现役「素材资产库」（/assets）并行的第二版。
 *
 * 并存期两个入口同时在导航里，互不影响；切换完成后再下线旧入口。
 * 权限用新前缀 `library:*`，与旧 `asset:*` 完全隔离：给旧库配的权限
 * 不会顺带打开 v2 的可见范围。
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
        label: '资产库 v2',
        icon: Library,
        group: '资产与数据',
        groupOrder: 3,
        order: 2,
      },
      card: {
        subtitle: '草稿 / 小组 / 团队',
        gradient: 'linear-gradient(140deg,#3ddc97,#12b3a0)',
        order: 4,
      },
    },
  ],
});
