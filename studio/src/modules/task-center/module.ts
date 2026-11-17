import { ListChecks } from 'lucide-react';
import { defineModule } from '@/shared/registry/define-module';
import { RunningTaskBadge } from './RunningTaskBadge';
import { TaskCompletionWatcher } from './TaskCompletionWatcher';

export default defineModule({
  id: 'task-center',
  title: '任务中心',
  routes: [
    {
      path: 'tasks',
      load: () => import('@/pages/task-center/TaskCenterPage'),
      permissions: ['task:read'],
      nav: {
        label: '任务中心',
        icon: ListChecks,
        group: '资产与数据',
        groupOrder: 3,
        order: 2,
        // 角标实现归模块所有；骨架不关心它怎么取数
        badge: RunningTaskBadge,
      },
    },
  ],
  /**
   * 生成完成时的到达提醒。挂在全局浮层插槽而不是某个页面里 —— 用户提交完
   * 会切走，提示要跟着人走。权限与「看得见任务中心」一致：看不到任务的人
   * 也不该收到任务通知。
   */
  slots: [
    {
      name: 'shell.overlay',
      component: TaskCompletionWatcher,
      permissions: ['task:read'],
    },
  ],
});
