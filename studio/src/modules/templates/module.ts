import { LayoutGrid, Layers3, PackagePlus } from 'lucide-react';
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
    {
      /* 封装入口单独一条：它写的是「配置」，与模板库「选模板」是两种动作。
         同一模块下挂两条路由，侧边栏因此出现第二项，权限也分开判。 */
      path: 'templates/manifests',
      load: () => import('@/pages/templates/ManifestsPage'),
      permissions: ['template_pack:read'],
      nav: {
        label: '模板封装',
        icon: PackagePlus,
        group: '模板与镜头',
        groupOrder: 2,
        order: 2,
      },
    },
    {
      /* 通用模板（槽位 + 提示词 + 模型）。与上面的「模板封装」是两种东西：
         那条写的是替换模板（原片 + 素材包），这条写的是任意生成模板。 */
      path: 'templates/specs',
      load: () => import('@/pages/templates/SpecsPage'),
      permissions: ['template_pack:read'],
      nav: {
        label: '模板规格',
        icon: Layers3,
        group: '模板与镜头',
        groupOrder: 2,
        order: 3,
      },
    },
  ],
});
