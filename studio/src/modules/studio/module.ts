import { Sparkles } from 'lucide-react';
import { defineModule } from '@/shared/registry/define-module';

/**
 * 模块清单示例：路由 + 侧边栏 + 顶部宫格卡片，三处一次声明。
 * 页面本身仍是懒加载（`load` 里才 import），不影响首屏体积。
 */
export default defineModule({
  id: 'studio',
  title: '生成工作台',
  routes: [{ path: 'studio', load: () => import('@/pages/studio/StudioPage') }],
  nav: [
    {
      label: '生成工作台',
      path: '/studio',
      icon: Sparkles,
      group: '创作',
      groupOrder: 1,
      order: 1,
    },
  ],
  cards: [
    {
      title: '生成工作台',
      subtitle: '描述→脚本→分镜→成片',
      path: '/studio',
      icon: Sparkles,
      gradient: 'linear-gradient(140deg,#5b8cff,#7c5cff)',
      order: 1,
      matchPrefixes: ['/studio', '/image'],
    },
  ],
});
