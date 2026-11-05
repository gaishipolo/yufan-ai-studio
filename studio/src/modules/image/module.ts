import { Image as ImageIcon } from 'lucide-react';
import { defineModule } from '@/shared/registry/define-module';

export default defineModule({
  id: 'image',
  title: '生图',
  routes: [{ path: 'image', load: () => import('@/pages/image/ImagePage') }],
  nav: [
    {
      label: '生图',
      path: '/image',
      icon: ImageIcon,
      group: '创作',
      groupOrder: 1,
      order: 2,
    },
  ],
});
