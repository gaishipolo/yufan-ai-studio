import { describe, expect, it } from 'vitest';
import { appRegistry } from './registry';

/**
 * 资产库 v2 的入口切换（M5），用**装配后的**注册表断言。
 *
 * 为什么放在 app 层：`src/modules/*` 之间禁止互相 import（多个模块的入口要放在
 * 一起比较），而装配结果本来就只有 `appRegistry` 知道。
 *
 * 这几个用例守的不是「代码长什么样」，而是观察期一旦被顺手改坏、症状又要等
 * 用户点错才出现的三件事：
 *
 * 1. 旧入口不能消失。观察期的回退路径必须是「点一下」，不是「改一行再重启」；
 * 2. 新入口排在旧入口前面，且旧入口文案带「旧版」。顺序反了用户会继续往旧库
 *    里传，两份数据各写一半，对账永远追不平；
 * 3. 旧路由的权限不能被顺手换成 `library:read`。换掉之后，只配了 `asset:*`
 *    的账号会突然打不开自己昨天还在用的页面。
 */
const navOf = (path: string) =>
  appRegistry.navGroups.flatMap((group) => group.items).find((item) => item.path === path);

describe('资产库 v2 入口切换', () => {
  it('新旧两个入口同时存在，旧入口文案标注「旧版」', () => {
    expect(navOf('/library')?.label).toBe('资产库');
    expect(navOf('/assets')?.label).toContain('旧版');
  });

  it('同一分组里新库排在旧库前面，且都在「资产与数据」组', () => {
    const group = appRegistry.navGroups.find((one) => one.title === '资产与数据');
    const paths = group?.items.map((item) => item.path) ?? [];
    expect(paths).toContain('/library');
    expect(paths).toContain('/assets');
    expect(paths.indexOf('/library')).toBeLessThan(paths.indexOf('/assets'));
  });

  it('首页宫格里新库也排在旧库前面', () => {
    const paths = appRegistry.cards.map((card) => card.path);
    expect(paths.indexOf('/library')).toBeLessThan(paths.indexOf('/assets'));
  });

  it('旧库路由与权限保持原样：/assets 仍要 asset:read', () => {
    const route = appRegistry.routes.find((one) => one.path === '/assets');
    expect(route?.permissions).toEqual(['asset:read']);
  });

  it('新库只要 library:read，不会顺带打开旧库', () => {
    const route = appRegistry.routes.find((one) => one.path === '/library');
    expect(route?.permissions).toEqual(['library:read']);
  });
});
