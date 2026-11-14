import { Layers3, Wand2 } from 'lucide-react';
import { defineModule } from '@/shared/registry/define-module';

/**
 * 模板与镜头：两条路由，两种动作。
 *
 * - `/templates/library`：**填表**——选一份已发布模板，把槽位填满，直接生成；
 * - `/templates/specs`：**封装**——定槽位、写提示词、选模型，发布后进模板库。
 *
 * 2026-11-14 P3 之后只剩这两条：「替换工作台」(`/templates`) 与「模板封装」
 * (`/templates/manifests`) 随替换链和旧封装表一起下线 —— 替换不再是平行链路，
 * 而是“某份模板 + 某个声明了替换能力的模型”的普通生成，落的就是任务中心那张表。
 */
export default defineModule({
  id: 'templates',
  title: '模板库',
  routes: [
    {
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
      card: {
        subtitle: '填素材即生成',
        gradient: 'linear-gradient(140deg,#9b8cff,#7c5cff)',
        order: 4,
        matchPrefixes: ['/templates', '/storyboards', '/shots'],
      },
    },
    {
      path: 'templates/specs',
      load: () => import('@/pages/templates/SpecsPage'),
      permissions: ['template_pack:read'],
      nav: {
        label: '模板封装',
        icon: Layers3,
        group: '模板与镜头',
        groupOrder: 2,
        order: 1,
      },
    },
  ],
});
