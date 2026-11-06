import { LayoutGrid } from 'lucide-react';
import { defineModule } from '@/shared/registry/define-module';

export default defineModule({
  id: 'templates',
  title: '模板库',
  routes: [
    {
      path: 'templates',
      load: () => import('@/pages/templates/TemplatesPage'),
      permissions: ['template:read'],
      nav: {
        label: '模板库',
        icon: LayoutGrid,
        group: '模板与镜头',
        groupOrder: 2,
        order: 1,
      },
      card: {
        subtitle: '跨产品复用',
        gradient: 'linear-gradient(140deg,#9b8cff,#7c5cff)',
        order: 4,
        matchPrefixes: ['/templates', '/storyboards', '/shots'],
      },
    },
  ],
});
