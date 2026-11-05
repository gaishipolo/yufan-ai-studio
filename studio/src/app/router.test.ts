import type { ReactElement } from 'react';
import { describe, expect, it } from 'vitest';
import { appRegistry } from './registry';
import { router } from './router';
import { RequirePermission } from '@/shared/access/RequirePermission';

/**
 * 路由装配的不变量：注册表 ⇄ router 必须一一对应，
 * 且每个页面都被权限守卫包住（否则声明了 permissions 也白搭）。
 */
interface RouteChild {
  path?: string;
  index?: boolean;
  element?: ReactElement;
}

const shell = router.routes[0];
const children = (shell.children ?? []) as RouteChild[];
const pageRoutes = children.filter((child) => child.path && child.path !== '*');

describe('router 装配', () => {
  it('页面路由数量与注册表一致', () => {
    const expected = appRegistry.routes.map((route) => route.path.replace(/^\//, '')).sort();
    expect(pageRoutes.map((child) => child.path).sort()).toEqual(expected);
  });

  it('/ 与未知路径都有兜底', () => {
    expect(children.some((child) => child.index)).toBe(true);
    expect(children.some((child) => child.path === '*')).toBe(true);
  });

  it('每个页面都被 RequirePermission 包裹', () => {
    for (const child of pageRoutes) {
      const element = child.element as ReactElement<{ children?: unknown }> | undefined;
      expect(element?.type, `路由 ${child.path} 没有元素`).toBe(RequirePermission);
      expect(element?.props.children).toBeTruthy();
    }
  });
});
