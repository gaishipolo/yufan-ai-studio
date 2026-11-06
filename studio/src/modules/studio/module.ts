import { Sparkles } from 'lucide-react';
import { defineModule } from '@/shared/registry/define-module';

/**
 * 模块清单示例：路由 + 侧边栏 + 顶部宫格卡片，一处声明、骨架自动派生。
 * 页面本身仍是懒加载（`load` 里才 import），不影响首屏体积。
 */
export default defineModule({
  id: 'studio',
  title: '生成工作台',
  routes: [
    {
      path: 'studio',
      load: () => import('@/pages/studio/StudioPage'),
      // 工作台要先读模型目录（GET /catalog/models）才能选模型与时长
      permissions: ['catalog:read'],
      nav: {
        label: '生成工作台',
        icon: Sparkles,
        group: '创作',
        groupOrder: 1,
        order: 1,
      },
      card: {
        subtitle: '描述→脚本→分镜→成片',
        gradient: 'linear-gradient(140deg,#5b8cff,#7c5cff)',
        order: 1,
        matchPrefixes: ['/studio', '/image'],
      },
    },
  ],
});
