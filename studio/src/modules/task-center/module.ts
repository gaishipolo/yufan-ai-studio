import { Layers, ListChecks } from 'lucide-react';
import { defineModule } from '@/shared/registry/define-module';
import { RunningTaskBadge } from './RunningTaskBadge';

export default defineModule({
  id: 'task-center',
  title: '任务中心',
  routes: [
    {
      path: 'tasks',
      load: () => import('@/pages/task-center/TaskCenterPage'),
      permissions: ['task:read'],
    },
  ],
  nav: [
    {
      label: '任务中心',
      path: '/tasks',
      icon: ListChecks,
      group: '资产与数据',
      groupOrder: 3,
      order: 2,
      permissions: ['task:read'],
      // 角标实现归模块所有；骨架不关心它怎么取数
      badge: RunningTaskBadge,
    },
  ],
  cards: [
    {
      title: '任务中心',
      subtitle: '进度与成片',
      path: '/tasks',
      icon: Layers,
      gradient: 'linear-gradient(140deg,#2fd6c4,#12b3c8)',
      order: 2,
      permissions: ['task:read'],
    },
  ],
});
