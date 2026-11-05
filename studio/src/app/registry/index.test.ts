import { describe, expect, it } from 'vitest';
import { appRegistry } from './index';

/**
 * 骨架级不变量：这里挂了说明「模块清单」和「注册表」脱节了，
 * 比等到用户点出 404 更早发现问题。
 */
describe('appRegistry · 骨架不变量', () => {
  const routePaths = new Set(appRegistry.routes.map((route) => route.path));
  const navItems = appRegistry.navGroups.flatMap((group) => group.items);

  it('至少装配出一个模块', () => {
    expect(appRegistry.modules.length).toBeGreaterThan(0);
    expect(appRegistry.routes.length).toBeGreaterThanOrEqual(appRegistry.modules.length);
  });

  it('每个导航项都指向已注册的路由', () => {
    for (const item of navItems) {
      expect(routePaths.has(item.path), `导航 ${item.label} → ${item.path} 没有路由`).toBe(true);
    }
  });

  it('每个宫格卡片都指向已注册的路由', () => {
    for (const card of appRegistry.cards) {
      expect(routePaths.has(card.path), `卡片 ${card.title} → ${card.path} 没有路由`).toBe(true);
    }
  });

  it('路由组件都已生成（lazy 包装成功）', () => {
    for (const route of appRegistry.routes) {
      expect(typeof route.Component, `路由 ${route.path} 缺少组件`).toBe('object');
    }
  });

  it('没有模块把根路径 / 注册成页面', () => {
    expect(routePaths.has('/')).toBe(false);
  });
});
