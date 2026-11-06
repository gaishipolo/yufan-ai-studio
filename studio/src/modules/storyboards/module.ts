import { Clapperboard } from 'lucide-react';
import { defineModule } from '@/shared/registry/define-module';

export default defineModule({
  id: 'storyboards',
  title: '分镜模板库',
  routes: [
    {
      path: 'storyboards',
      load: () => import('@/pages/storyboards/StoryboardsPage'),
      permissions: ['storyboard:read'],
      nav: {
        label: '分镜模板库',
        icon: Clapperboard,
        group: '模板与镜头',
        groupOrder: 2,
        order: 2,
      },
    },
  ],
});
