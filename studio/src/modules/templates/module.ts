import { LayoutGrid, Layers3, PackagePlus, Wand2 } from 'lucide-react';
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
        /* 这条是**替换工作台**（原片 + 素材包替换），不是新的「模板库填表」。
           P3 收编后它下线，届时只剩 `/templates/library` 一条。 */
        label: '替换工作台',
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
      /* 填表入口：选已发布模板 → 填素材 → 生成。与「模板库」原页面（替换工作台）
         是两种动作，所以单开一条；P3 收编后替换工作台下线，这条就是唯一的模板库。 */
      path: 'templates/library',
      load: () => import('@/pages/templates/LibraryPage'),
      permissions: ['template_pack:read'],
      nav: {
        label: '模板库',
        icon: Wand2,
        group: '模板与镜头',
        groupOrder: 2,
        order: 0,
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
