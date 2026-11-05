import { useEffect, useId, useRef } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { ChevronDown } from 'lucide-react';
import { can } from '@/shared/access/evaluate';
import { useSubject } from '@/shared/access/use-access';
import { cn } from '@/shared/lib/cn';
import { isPathActive } from '@/shared/lib/path';
import { Slot } from '@/shared/registry/Slot';
import { useRegistry } from '@/shared/registry/use-registry';
import type { NavGroup, ResolvedNavItem } from '@/shared/registry/types';
import { useCollapsedGroups } from './use-collapsed-groups';

/**
 * 单个导航项。
 * 权限由分组统一过滤，角标由「模块清单」提供 ——
 * 新增一个入口 = 新增一条 nav 声明，这里零改动。
 */
function SidebarItem({ item }: { item: ResolvedNavItem }) {
  const Icon = item.icon;
  const Badge = item.badge;

  return (
    <NavLink
      to={item.path}
      className={({ isActive }) =>
        cn(
          'relative flex h-[33px] shrink-0 items-center gap-2 whitespace-nowrap rounded-[9px] px-2.5 text-xs transition-colors',
          isActive
            ? 'bg-panel font-semibold text-primary shadow-card'
            : 'text-ink-soft hover:bg-[rgba(255,255,255,.75)] hover:text-ink',
        )
      }
    >
      {({ isActive }) => (
        <>
          {isActive && (
            <span className="absolute -left-px bottom-2 top-2 w-[3px] rounded-[3px] bg-primary max-md:hidden" />
          )}
          <Icon size={15} className={isActive ? 'text-primary' : 'text-[#9fb0b8]'} />
          <span>{item.label}</span>
          {Badge && <Badge />}
        </>
      )}
    </NavLink>
  );
}

/**
 * 一个一级类目（分组）。
 * 折叠是骨架行为，模块只声明 `group` 字符串，不感知折叠；默认展开。
 * 移动端侧栏是横向滚动条，折叠只在 md+ 生效，避免退化成一堆只剩标题的按钮。
 */
function SidebarGroup({
  group,
  items,
  panelId,
  collapsed,
  hasActive,
  onToggle,
}: {
  group: NavGroup;
  items: ResolvedNavItem[];
  panelId: string;
  collapsed: boolean;
  hasActive: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="flex shrink-0 items-center gap-1.5 md:mb-3.5 md:block">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={!collapsed}
        aria-controls={panelId}
        className={cn(
          'hidden w-full items-center gap-1 rounded-[6px] px-2.5 pb-1.5 pt-3 text-left',
          'text-[10.5px] font-bold tracking-[.08em] text-[#a4b3bb] transition-colors md:flex',
          'hover:text-ink-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40',
          hasActive && !collapsed && 'text-ink-soft',
        )}
      >
        <ChevronDown
          size={11}
          strokeWidth={2.6}
          aria-hidden
          className={cn('shrink-0 transition-transform duration-200', collapsed && '-rotate-90')}
        />
        <span className="truncate">{group.title}</span>
        {/* 收起状态下用一个小圆点提示「当前页就在这里面」 */}
        {collapsed && hasActive && (
          <span className="ml-0.5 h-1.5 w-1.5 shrink-0 rounded-full bg-primary" aria-hidden />
        )}
      </button>

      <div
        id={panelId}
        role="group"
        aria-label={group.title}
        className={cn(
          'flex items-center gap-1.5 md:flex-col md:items-stretch md:gap-0',
          collapsed && 'md:hidden',
        )}
      >
        {items.map((item) => (
          <SidebarItem key={item.path} item={item} />
        ))}
      </div>
    </div>
  );
}

export function Sidebar() {
  const { navGroups } = useRegistry();
  const { pathname } = useLocation();
  const subject = useSubject();
  const { isCollapsed, toggle, expand } = useCollapsedGroups();
  const groupIdPrefix = useId();

  // 无可见项的分组不渲染标题 —— 否则会留下一个点了没反应的折叠头
  const groups = navGroups
    .map((group) => ({
      group,
      items: group.items.filter((item) => can(subject, item.permissions)),
    }))
    .filter((entry) => entry.items.length > 0);

  // 命中当前路由的分组：用于自动展开 + 折叠态高亮
  const activeGroup = groups.find((entry) =>
    entry.items.some((item) => isPathActive(pathname, item.path)),
  )?.group.title;

  // 通过顶部宫格 / 手输 URL 跳到某个分组下的页面时自动展开，
  // 避免「跳过去了却看不到高亮项」。首屏不展开：尊重用户上次的折叠结果。
  const previousPath = useRef(pathname);
  useEffect(() => {
    if (previousPath.current === pathname) return;
    previousPath.current = pathname;
    if (activeGroup) expand(activeGroup);
  }, [pathname, activeGroup, expand]);

  return (
    <aside
      className={cn(
        'flex gap-1.5 overflow-x-auto border-line pb-2.5 pt-1.5 scrollbar-none',
        'md:sticky md:top-[62px] md:flex-col md:gap-0 md:overflow-visible md:border-0 md:pb-5',
      )}
    >
      {groups.map((entry, index) => (
        <SidebarGroup
          key={entry.group.title}
          group={entry.group}
          items={entry.items}
          panelId={`${groupIdPrefix}-nav-${index}`}
          collapsed={isCollapsed(entry.group.title)}
          hasActive={entry.group.title === activeGroup}
          onToggle={() => toggle(entry.group.title)}
        />
      ))}

      {/* 侧栏底部插槽：版本号 / 帮助入口等由模块自行注入，不用改 Sidebar */}
      <div className="hidden md:mt-auto md:block">
        <Slot name="sidebar.footer" />
      </div>
    </aside>
  );
}
