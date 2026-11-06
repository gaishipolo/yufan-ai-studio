import type { ReactElement } from 'react';
import { describe, expect, it } from 'vitest';
import { appRegistry } from './registry';
import { router } from './router';
import { RequireAuth } from '@/shared/access/RequireAuth';
import { RequirePermission } from '@/shared/access/RequirePermission';
import { isResolvablePermission } from '@/entities/role/model/permission-catalog';

/**
 * 路由装配的不变量：
 * 1. 注册表 ⇄ router 一一对应（shell 路由进外壳，bare 路由在顶层）；
 * 2. 每个页面都被权限守卫包住（否则声明了 permissions 也白搭）；
 * 3. 外壳被登录守卫包住，bare 页面不被包（否则登录页会被守卫送回自己）。
 */
interface RouteChild {
  path?: string;
  index?: boolean;
  element?: ReactElement;
}

/* 路由对象是 Data Router 的内部结构（AgnosticDataRouteObject），不含 element，
   这里按实际读取的字段收窄一次，避免每个断言都写断言 */
const shell = router.routes[0] as unknown as RouteChild & { children?: RouteChild[] };
const children = shell.children ?? [];
const pageRoutes = children.filter((child) => child.path && child.path !== '*');

const bareRoutes = appRegistry.routes.filter((route) => route.layout === 'bare');
const shellPageRoutes = appRegistry.routes.filter((route) => route.layout === 'shell');

/**
 * 明确豁免「必须声明权限」的页面。
 *
 * 写成显式数组而不是"空 permissions 就跳过"，是为了让每一次豁免都留下理由：
 * 后端还没有对应权限点时，写上权限码只会让页面永久打不开（fail-closed 且静默）。
 * 目前只有用量看板（占位页，后端无 `usage:read`）。
 */
const NO_PERMISSION_PATHS = new Set(['/usage']);

/** 顶层路由里除 `'/'` 之外的那批（即 bare 页面） */
const topLevelOthers = router.routes.filter(
  (route) => route.path !== '/',
) as unknown as RouteChild[];

describe('router 装配', () => {
  it('外壳内页面的路径与注册表中 layout=shell 的路由一致', () => {
    const expected = shellPageRoutes.map((route) => route.path.replace(/^\//, '')).sort();
    expect(pageRoutes.map((child) => child.path).sort()).toEqual(expected);
  });

  it('独立布局页面作为顶层路由存在，且不在外壳内', () => {
    expect(bareRoutes.length).toBeGreaterThan(0);
    expect(topLevelOthers.map((route) => route.path).sort()).toEqual(
      bareRoutes.map((route) => route.path).sort(),
    );

    const shellPaths = new Set(pageRoutes.map((child) => child.path));
    for (const route of bareRoutes) {
      expect(shellPaths.has(route.path.replace(/^\//, ''))).toBe(false);
    }
  });

  it('/ 与未知路径都有兜底', () => {
    expect(children.some((child) => child.index)).toBe(true);
    expect(children.some((child) => child.path === '*')).toBe(true);
  });

  it('每个外壳内页面都被 RequirePermission 包裹', () => {
    for (const child of pageRoutes) {
      const element = child.element as ReactElement<{ children?: unknown }> | undefined;
      expect(element?.type, `路由 ${child.path} 没有元素`).toBe(RequirePermission);
      expect(element?.props.children).toBeTruthy();
    }
  });

  /* 上面那条只检查「有没有被守卫包住」，不检查守卫里有没有东西：
     `can(subject, [])` 恒为 true，所以一条 permissions 都不写的页面
     对任何登录用户都放行，而包裹关系看起来完全正常。
     这里把「必须声明权限」也变成断言，新增模块时会被迫想一次「这页要不要门禁」。 */
  it('除显式豁免外，每个外壳页面都声明了权限点', () => {
    for (const route of shellPageRoutes) {
      if (NO_PERMISSION_PATHS.has(route.path)) {
        // 反向断言：豁免项一旦补上权限，这条名单就该删掉，否则豁免会永远留着
        expect(route.permissions, `${route.path} 已声明权限，请从豁免名单移除`).toHaveLength(0);
        continue;
      }
      expect(
        route.permissions.length,
        `路由 ${route.path} 没有声明 permissions —— 任何登录用户都能直接访问`,
      ).toBeGreaterThan(0);
    }
  });

  it('外壳被 RequireAuth 包裹，登录页不被包裹', () => {
    const shellElement = shell.element as ReactElement<{ children?: unknown }> | undefined;
    expect(shellElement?.type).toBe(RequireAuth);

    for (const route of topLevelOthers) {
      const element = route.element as ReactElement<{ children?: unknown }> | undefined;
      expect(element?.type, `独立路由 ${route.path} 不应套登录守卫`).not.toBe(RequireAuth);
    }
  });

  /* 权限码拼错不会报错，只会让那个入口对**所有人**永久隐藏（fail-closed 且静默）。
     目录内容由后端生成物 + 字节比对守卫保证，这里只负责"声明的点确实存在"。 */
  it('模块声明的权限点都在权限目录里', () => {
    expect(appRegistry.declaredPermissions.length).toBeGreaterThan(0);

    for (const permission of appRegistry.declaredPermissions) {
      expect(isResolvablePermission(permission), `权限点 ${permission} 不在目录里（检查拼写）`).toBe(
        true,
      );
    }
  });
});
