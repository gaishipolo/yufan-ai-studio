import { FolderOpen } from 'lucide-react';
import { defineModule } from '@/shared/registry/define-module';

export default defineModule({
  id: 'assets',
  title: '素材资产库',
  routes: [
    {
      path: 'assets',
      load: () => import('@/pages/assets/AssetsPage'),
      permissions: ['asset:read'],
      nav: {
        label: '素材资产库',
        icon: FolderOpen,
        group: '资产与数据',
        groupOrder: 3,
        order: 1,
      },
      card: {
        subtitle: '图 / 视频 / 音频',
        gradient: 'linear-gradient(140deg,#ffd24a,#f5a623)',
        order: 3,
      },
    },
  ],
});
