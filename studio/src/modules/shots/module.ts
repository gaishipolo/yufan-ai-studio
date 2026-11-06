import { Video } from 'lucide-react';
import { defineModule } from '@/shared/registry/define-module';

export default defineModule({
  id: 'shots',
  title: '镜头库',
  routes: [
    {
      path: 'shots',
      load: () => import('@/pages/shots/ShotsPage'),
      permissions: ['shot:read'],
      nav: {
        label: '镜头库',
        icon: Video,
        group: '模板与镜头',
        groupOrder: 2,
        order: 3,
      },
    },
  ],
});
