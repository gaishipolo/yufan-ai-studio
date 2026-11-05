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
    },
  ],
  nav: [
    {
      label: '模板库',
      path: '/templates',
      icon: LayoutGrid,
      group: '模板与镜头',
      groupOrder: 2,
      order: 1,
      permissions: ['template:read'],
    },
  ],
  cards: [
    {
      title: '模板库',
      subtitle: '跨产品复用',
      path: '/templates',
      icon: LayoutGrid,
      gradient: 'linear-gradient(140deg,#9b8cff,#7c5cff)',
      order: 4,
      permissions: ['template:read'],
      matchPrefixes: ['/templates', '/storyboards', '/shots'],
    },
  ],
});
