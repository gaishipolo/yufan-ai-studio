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

      routes.push({
        path,
        Component: lazy(def.load),
        permissions,
        moduleId: mod.id,
      });
    }

    for (const nav of mod.nav ?? []) {
      const path = normalizePath(nav.path);
      const owner = navOwners.get(path);
      if (owner) fail(`导航项 ${path} 在 ${owner} 与 ${mod.id} 中重复`);
      navOwners.set(path, mod.id);

      const permissions = nav.permissions ?? [];
      permissions.forEach((p) => declaredPermissions.add(p));

      navItems.push({ ...nav, path, permissions, moduleId: mod.id });
    }

    for (const card of mod.cards ?? []) {
      const permissions = card.permissions ?? [];
      permissions.forEach((p) => declaredPermissions.add(p));
      cards.push({
        ...card,
        path: normalizePath(card.path),
        matchPrefixes: card.matchPrefixes ?? [normalizePath(card.path)],
        permissions,
        moduleId: mod.id,
        seq: seq++,
      });
    }

    for (const slot of mod.slots ?? []) {
      const permissions = slot.permissions ?? [];
      permissions.forEach((p) => declaredPermissions.add(p));
      slots.push({ ...slot, order: slot.order ?? DEFAULT_ORDER, permissions, moduleId: mod.id, seq: seq++ });
    }
  }

  /* 导航必须指向真实存在的路由，否则点击必然 404 —— 注册期就拦掉 */
  for (const nav of navItems) {
    if (!routeOwners.has(nav.path)) {
      fail(
        `导航项「${nav.label}」(${nav.path}) 没有对应路由。` +
          `已注册路由：${[...routeOwners.keys()].join(', ')}`,
      );
    }
  }

  const navGroups = buildNavGroups(navItems);

  const sortedCards = cards
    .slice()
    .sort((a, b) => (a.order ?? DEFAULT_ORDER) - (b.order ?? DEFAULT_ORDER) || a.seq - b.seq)
    .map(({ seq: _seq, ...card }) => card);

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
