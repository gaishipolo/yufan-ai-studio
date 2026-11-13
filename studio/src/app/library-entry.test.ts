import { describe, expect, it } from 'vitest';
import { appRegistry } from './registry';

/**
 * 资产库只有一个入口：`/library`（v2）。用**装配后的**注册表断言。
 *
 * 为什么放在 app 层：`src/modules/*` 之间禁止互相 import（多个模块的入口要放在
 * 一起比较），而装配结果本来就只有 `appRegistry` 知道。
 *
 * 这三条守的是 v1 下线后**不能再回来**的路径。以前这里守的是「新旧两个入口同时
 * 存在、旧入口标『旧版』、旧路由仍要 `asset:read`」——那套是并存观察期留给用户
 * 的回退路。2026-11-13 `assets` 表与 `modules/asset` 一起删掉之后，「退回去」已经
 * 不是选项：任何一条残留的 `/assets` 入口点进去只会 404。
 */
const navOf = (path: string) =>
  appRegistry.navGroups.flatMap((group) => group.items).find((item) => item.path === path);

describe('资产库入口', () => {
  it('只剩 /library 一个入口', () => {
    expect(navOf('/library')?.label).toBe('资产库');
    expect(navOf('/assets')).toBeUndefined();
  });

  it('侧栏与首页宫格里都没有 /assets', () => {
    const navPaths = appRegistry.navGroups.flatMap((group) => group.items).map((item) => item.path);
    expect(navPaths).toContain('/library');
    expect(navPaths).not.toContain('/assets');
    expect(appRegistry.cards.map((card) => card.path)).not.toContain('/assets');
  });

  it('没有路由挂在 /assets 上，/library 只要 library:read', () => {
    expect(appRegistry.routes.map((route) => route.path)).not.toContain('/assets');
    expect(appRegistry.routes.find((one) => one.path === '/library')?.permissions).toEqual([
      'library:read',
    ]);
  });
});
