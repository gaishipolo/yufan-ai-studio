import { Package } from 'lucide-react';
import { defineModule } from '@/shared/registry/define-module';

export default defineModule({
  id: 'template-pack',
  title: '封装模板',
  routes: [
    {
      path: 'template-packs',
      load: () => import('@/pages/template-pack/TemplatePacksPage'),
      permissions: ['template_pack:read'],
      nav: {
        label: '封装模板',
        icon: Package,
        group: '模板与镜头',
        groupOrder: 2,
        order: 2,
      },
      card: {
        subtitle: '带筛选约束的模板',
        gradient: 'linear-gradient(140deg,#5ac8fa,#2b7cff)',
        order: 5,
        matchPrefixes: ['/template-packs'],
      },
    },
    /* 顺序要紧：`new` 必须排在 `:packId` 前面，
       否则 "new" 会被当成 packId 吃掉。 */
    {
      path: 'template-packs/new',
      load: () => import('@/pages/template-pack/TemplatePackEditorPage'),
      permissions: ['template_pack:create'],
    },
    {
      path: 'template-packs/:packId/edit',
      load: () => import('@/pages/template-pack/TemplatePackEditorPage'),
      permissions: ['template_pack:update'],
    },
    {
      path: 'template-packs/:packId',
      load: () => import('@/pages/template-pack/TemplatePackDetailPage'),
      permissions: ['template_pack:read'],
    },
  ],
});
