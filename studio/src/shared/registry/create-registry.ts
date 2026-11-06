import { lazy } from 'react';
import type {
  AppModule,
  AppRegistry,
  ModuleRouteDef,
  NavGroup,
  Permission,
  ResolvedCard,
  ResolvedNavItem,
  ResolvedRoute,
  ResolvedSlot,
  SlotName,
} from './types';

/** 未显式指定排序时的默认值：数值越小越靠前 */
const DEFAULT_ORDER = 100;

function normalizePath(path: string): string {
  const trimmed = path.trim();
  if (!trimmed) return '/';
  return trimmed.startsWith('/') ? trimmed : `/${trimmed}`;
}

function fail(message: string): never {
  throw new Error(`[registry] ${message}`);
}

/**
 * 把若干模块声明的碎片「编译」成路由表 / 导航树 / 插槽表。
 *
 * 这里是纯函数：不读全局变量、不碰 React 运行时状态，因此可以被单测直接驱动。
 * 所有校验都在注册期完成 —— 配错了会立刻在启动时抛错，而不是运行时白屏。
 *
 * 侧栏入口与宫格卡片不再是平级数组，而是挂在 `route.nav` / `route.card` 上：
 * path / permissions 从这里继承，模块只写展示差异。这样「改权限漏一处」
 * 从结构上就不可能发生，也就不必再靠测试去比对三处是否一致。
 */
export function createRegistry(modules: AppModule[]): AppRegistry {
  const moduleIds = new Set<string>();
  const routeOwners = new Map<string, string>();
  const navOwners = new Map<string, string>();
  const routes: ResolvedRoute[] = [];
  const navItems: ResolvedNavItem[] = [];
  const cards: Array<ResolvedCard & { seq: number }> = [];
  const slots: Array<ResolvedSlot & { seq: number }> = [];
  const declaredPermissions = new Set<Permission>();
  let seq = 0;

  for (const mod of modules) {
    if (!mod.id) fail('模块缺少 id');
    if (moduleIds.has(mod.id)) fail(`模块 id 重复：${mod.id}`);
    moduleIds.add(mod.id);

    if (!mod.routes.length) fail(`模块 ${mod.id} 至少需要声明一条路由`);

    for (const def of mod.routes) {
      assertRouteDef(mod, def);
      const path = normalizePath(def.path);
      const owner = routeOwners.get(path);
      if (owner) fail(`路由 ${path} 被 ${owner} 与 ${mod.id} 同时声明`);
      routeOwners.set(path, mod.id);

      const permissions = def.permissions ?? [];
      permissions.forEach((p) => declaredPermissions.add(p));

      const layout = def.layout ?? 'shell';

      routes.push({
        path,
        Component: lazy(def.load),
        permissions,
        layout,
        moduleId: mod.id,
      });

      /* 侧栏入口：path 与 permissions 继承本路由，模块只写 label / icon / group 等展示信息 */
      if (def.nav) {
        const navOwner = navOwners.get(path);
        if (navOwner) fail(`导航项 ${path} 在 ${navOwner} 与 ${mod.id} 中重复`);
        navOwners.set(path, mod.id);
        navItems.push({ ...def.nav, path, permissions, moduleId: mod.id });
      }

      /* 宫格卡片：同上；title / icon 缺省取 nav，再兜底到模块 title */
      if (def.card) {
        const icon = def.card.icon ?? def.nav?.icon;
        if (!icon) {
          fail(
            `模块 ${mod.id} 的宫格卡片 ${path} 缺少 icon：请补 card.icon，或在同一路由声明 nav.icon`,
          );
        }
        cards.push({
          ...def.card,
          title: def.card.title ?? def.nav?.label ?? mod.title ?? path,
          icon,
          path,
          permissions,
          matchPrefixes: def.card.matchPrefixes ?? [path],
          moduleId: mod.id,
          seq: seq++,
        });
      }
    }

    for (const slot of mod.slots ?? []) {
      const permissions = slot.permissions ?? [];
      permissions.forEach((p) => declaredPermissions.add(p));
      slots.push({ ...slot, order: slot.order ?? DEFAULT_ORDER, permissions, moduleId: mod.id, seq: seq++ });
    }
  }

  const navGroups = buildNavGroups(navItems);

  const sortedCards = cards
    .slice()
    .sort((a, b) => (a.order ?? DEFAULT_ORDER) - (b.order ?? DEFAULT_ORDER) || a.seq - b.seq)
    .map(({ seq: _seq, ...card }) => card);

  /* 注册期校验：导航 / 卡片指向真实存在的路由、且不指向裸页。
     派生之后这两条由结构保证（path 就是 route.path，bare 页在 assertRouteDef 里禁止声明），
     但保留成断言，防止以后有人为了"灵活"把 path 改回可写而悄悄退化。 */
  const barePaths = new Set(routes.filter((route) => route.layout === 'bare').map((route) => route.path));
  const knownPaths = new Set(routes.map((route) => route.path));
  for (const nav of navItems) {
    if (!knownPaths.has(nav.path)) {
      fail(`导航项「${nav.label}」(${nav.path}) 没有对应路由。`);
    }
    if (barePaths.has(nav.path)) {
      fail(`导航项「${nav.label}」指向独立页面 ${nav.path}（layout: 'bare'）。`);
    }
  }
  for (const card of sortedCards) {
    if (!knownPaths.has(card.path)) {
      fail(`宫格卡片「${card.title}」(${card.path}) 没有对应路由。`);
    }
    if (barePaths.has(card.path)) {
      fail(`宫格卡片「${card.title}」指向独立页面 ${card.path}（layout: 'bare'）。`);
    }
  }

  const slotMap: Partial<Record<SlotName, ResolvedSlot[]>> = {};
  for (const slot of slots
    .slice()
    .sort((a, b) => a.order - b.order || a.seq - b.seq)
    .map(({ seq: _seq, ...rest }) => rest)) {
    (slotMap[slot.name] ??= []).push(slot);
  }

  return {
    modules,
    routes,
    navGroups,
    cards: sortedCards,
    slots: slotMap,
    declaredPermissions: [...declaredPermissions].sort(),
  };
}

function assertRouteDef(mod: AppModule, def: ModuleRouteDef) {
  if (!def.path.trim()) {
    fail(`模块 ${mod.id} 的 route.path 不能为空字符串；首页请在 src/app/config.ts 里配置 HOME_PATH`);
  }
  if (typeof def.load !== 'function') {
    fail(`模块 ${mod.id} 的路由 ${def.path} 缺少 load()；请写成 load: () => import('@/pages/xxx/XxxPage')`);
  }
  if (def.layout !== undefined && def.layout !== 'shell' && def.layout !== 'bare') {
    fail(`模块 ${mod.id} 的路由 ${def.path} 的 layout 只能是 'shell' 或 'bare'`);
  }
  if (def.layout === 'bare' && (def.nav || def.card)) {
    fail(
      `模块 ${mod.id} 的独立页面 ${def.path} 不能声明 nav / card。` +
        `bare 页面不套外壳，放进侧栏或宫格会失去导航与登录态。`,
    );
  }
}

function buildNavGroups(items: ResolvedNavItem[]): NavGroup[] {
  const groups = new Map<string, { title: string; order: number; seq: number; items: ResolvedNavItem[] }>();

  items.forEach((item, index) => {
    const key = item.group;
    const order = item.groupOrder ?? DEFAULT_ORDER;
    const group = groups.get(key);
    if (group) {
      group.order = Math.min(group.order, order);
      group.items.push(item);
    } else {
      groups.set(key, { title: key, order, seq: index, items: [item] });
    }
  });

  return [...groups.values()]
    .sort((a, b) => a.order - b.order || a.seq - b.seq)
    .map((group) => ({
      title: group.title,
      order: group.order,
      items: group.items.sort((a, b) => (a.order ?? DEFAULT_ORDER) - (b.order ?? DEFAULT_ORDER)),
    }));
}
