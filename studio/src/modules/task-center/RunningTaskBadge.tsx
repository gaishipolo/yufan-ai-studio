import { useTaskFeed } from '@/entities/task/api/task.queries';

/**
 * 导航角标：由模块自己实现并挂到 nav.badge 上。
 * 骨架只负责「在导航项右侧留个位置」，因此新增角标不用改 Sidebar。
 *
 * 只取统计（`statsOnly`）：这个组件在每个页面的侧栏都挂载，
 * 拉完整列表会为每条替换档案组装标题与输入摘要。
 */
export function RunningTaskBadge() {
  const { data } = useTaskFeed({scope: 'mine', pageSize: 1, statsOnly: true});
  const running = data?.stats.running ?? 0;
  if (running <= 0) return null;

  return (
    <span className="ml-auto rounded-md bg-primary-soft px-1.5 text-[10px] font-bold text-primary">
      {running}
    </span>
  );
}
