import { Boxes } from 'lucide-react';
import { describe, expect, it } from 'vitest';
import { createRegistry } from './create-registry';
import { defineModule } from './define-module';
import type { AppModule } from './types';

const load = () => Promise.resolve({ default: () => null });

function moduleOf(overrides: Partial<AppModule> & Pick<AppModule, 'id'>): AppModule {
  return defineModule({
    routes: [{ path: overrides.id, load }],
    ...overrides,
  });
}

describe('createRegistry · 装配', () => {
  it('把相对路径归一化为绝对路径', () => {
    const registry = createRegistry([
      moduleOf({ id: 'a' }),
      moduleOf({ id: 'b', routes: [{ path: '/explicit', load }] }),
    ]);

    expect(registry.routes.map((r) => r.path)).toEqual(['/a', '/explicit']);
  });

  it('导航按 groupOrder → 组内 order → 注册顺序 排序', () => {
    const registry = createRegistry([
      moduleOf({
        id: 'late',
        nav: [{ label: '后台', path: '/late', icon: Boxes, group: '系统', groupOrder: 9 }],
      }),
      moduleOf({
        id: 'a',
        nav: [{ label: 'A', path: '/a', icon: Boxes, group: '创作', groupOrder: 1, order: 2 }],
      }),
      moduleOf({
        id: 'b',
        nav: [{ label: 'B', path: '/b', icon: Boxes, group: '创作', groupOrder: 1, order: 1 }],
      }),
    ]);

    expect(registry.navGroups.map((g) => g.title)).toEqual(['创作', '系统']);
    expect(registry.navGroups[0].items.map((i) => i.label)).toEqual(['B', 'A']);
  });

  it('宫格卡片缺省 matchPrefixes 时用自身路径', () => {
    const registry = createRegistry([
      moduleOf({
        id: 'a',
        cards: [
          { title: 'A', subtitle: 's', path: '/a', icon: Boxes, gradient: 'x' },
        ],
      }),
    ]);

    expect(registry.cards[0].matchPrefixes).toEqual(['/a']);
  });

  it('收集所有声明过的权限，并挂到对应路由上', () => {
    const registry = createRegistry([
      moduleOf({
        id: 'a',
        routes: [{ path: 'a', load, permissions: ['user:manage'] }],
        nav: [{ label: 'A', path: '/a', icon: Boxes, group: '系统', permissions: ['user:read'] }],
      }),
    ]);

    expect(registry.routes[0].permissions).toEqual(['user:manage']);
    expect(registry.declaredPermissions).toEqual(['user:manage', 'user:read']);
  });

  it('插槽按 order 排序，缺省排到最后', () => {
    const registry = createRegistry([
      moduleOf({
        id: 'a',
        slots: [
          { name: 'topbar.actions', component: () => null, order: 5 },
          { name: 'topbar.actions', component: () => null },
          { name: 'topbar.actions', component: () => null, order: 1 },
        ],
      }),
    ]);

    expect(registry.slots['topbar.actions']?.map((s) => s.order)).toEqual([1, 5, 100]);
  });
});

describe('createRegistry · 校验（宁可启动报错，不要运行时白屏）', () => {
  it('模块 id 重复 → 抛错', () => {
    expect(() => createRegistry([moduleOf({ id: 'dup' }), moduleOf({ id: 'dup' })])).toThrow(
      /模块 id 重复：dup/,
    );
  });

  it('路由重复 → 抛错并指出双方', () => {
    expect(() =>
      createRegistry([
        moduleOf({ id: 'a', routes: [{ path: 'same', load }] }),
        moduleOf({ id: 'b', routes: [{ path: '/same', load }] }),
      ]),
    ).toThrow(/路由 \/same 被 a 与 b 同时声明/);
  });

  it('导航指向不存在的路由 → 抛错', () => {
    expect(() =>
      createRegistry([
        moduleOf({
          id: 'a',
          nav: [{ label: '幽灵', path: '/ghost', icon: Boxes, group: '系统' }],
        }),
      ]),
    ).toThrow(/导航项「幽灵」\(\/ghost\) 没有对应路由/);
  });

  /* 卡片和导航是同一族入口，以前只有 nav 校验：
     改模块 id 之后卡片还能通过注册，用户点首页宫格才 404 */
  it('宫格卡片指向不存在的路由 → 抛错', () => {
    expect(() =>
      createRegistry([
        moduleOf({
          id: 'a',
          cards: [{ title: '幽灵卡片', subtitle: 's', path: '/nope', icon: Boxes, gradient: 'x' }],
        }),
      ]),
    ).toThrow(/宫格卡片「幽灵卡片」\(\/nope\) 没有对应路由/);
  });

  it('宫格卡片指向独立页面 → 抛错（裸页放进宫格会丢外壳）', () => {
    expect(() =>
      createRegistry([
        moduleOf({
          id: 'a',
          routes: [{ path: 'login', layout: 'bare', load }],
          cards: [{ title: '登录卡', subtitle: 's', path: '/login', icon: Boxes, gradient: 'x' }],
        }),
      ]),
    ).toThrow(/宫格卡片「登录卡」指向独立页面 \/login/);
  });

  it('route.path 为空 —— 根路由只能由 HOME_PATH 决定', () => {
    expect(() => createRegistry([moduleOf({ id: 'a', routes: [{ path: '', load }] })])).toThrow(
      /不能为空字符串/,
    );
  });

  it('模块没有任何路由 → 抛错（多半是忘了声明）', () => {
    expect(() => createRegistry([moduleOf({ id: 'a', routes: [] })])).toThrow(/至少需要声明一条路由/);
  });
});
