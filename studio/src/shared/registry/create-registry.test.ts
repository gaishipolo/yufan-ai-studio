import { Boxes, Layers } from 'lucide-react';
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
        routes: [
          {
            path: 'late',
            load,
            nav: { label: '后台', icon: Boxes, group: '系统', groupOrder: 9 },
          },
        ],
      }),
      moduleOf({
        id: 'a',
        routes: [
          {
            path: 'a',
            load,
            nav: { label: 'A', icon: Boxes, group: '创作', groupOrder: 1, order: 2 },
          },
        ],
      }),
      moduleOf({
        id: 'b',
        routes: [
          {
            path: 'b',
            load,
            nav: { label: 'B', icon: Boxes, group: '创作', groupOrder: 1, order: 1 },
          },
        ],
      }),
    ]);

    expect(registry.navGroups.map((g) => g.title)).toEqual(['创作', '系统']);
    expect(registry.navGroups[0].items.map((i) => i.label)).toEqual(['B', 'A']);
  });

  it('宫格卡片缺省 matchPrefixes 时用自身路径', () => {
    const registry = createRegistry([
      moduleOf({
        id: 'a',
        routes: [
          {
            path: 'a',
            load,
            nav: { label: 'A', icon: Boxes, group: '创作' },
            card: { subtitle: 's', gradient: 'x' },
          },
        ],
      }),
    ]);

    expect(registry.cards[0].matchPrefixes).toEqual(['/a']);
  });

  /* B2：以前 path 与 permissions 要在 route / nav / cards 里各写一遍，
     改权限漏一处就是「入口可见但点进去 403」。现在由注册表继承，
     这个用例把「派生」本身钉住——不是靠人记得同步。 */
  it('route.nav / route.card 的 path 与 permissions 一律继承 route', () => {
    const registry = createRegistry([
      moduleOf({
        id: 'a',
        title: '甲模块',
        routes: [
          {
            path: 'a',
            load,
            permissions: ['user:manage'],
            nav: { label: '侧栏甲', icon: Boxes, group: '系统' },
            card: { subtitle: 's', gradient: 'x' },
          },
        ],
      }),
    ]);

    const nav = registry.navGroups[0].items[0];
    const card = registry.cards[0];
    expect(nav.path).toBe('/a');
    expect(nav.permissions).toEqual(['user:manage']);
    expect(card.path).toBe('/a');
    expect(card.permissions).toEqual(['user:manage']);
    // title / icon 从 nav 继承，没写第二遍
    expect(card.title).toBe('侧栏甲');
    expect(card.icon).toBe(Boxes);
  });

  it('card 可以单独指定 icon / title 覆盖 nav，不写 nav 时兜底到模块 title', () => {
    const registry = createRegistry([
      moduleOf({
        id: 'a',
        title: '甲模块',
        routes: [
          {
            path: 'a',
            load,
            nav: { label: '侧栏甲', icon: Boxes, group: '系统' },
            card: { subtitle: 's', gradient: 'x', icon: Layers, title: '宫格甲' },
          },
        ],
      }),
      moduleOf({
        id: 'b',
        title: '乙模块',
        routes: [
          { path: 'b', load, card: { subtitle: 's', gradient: 'x', icon: Layers } },
        ],
      }),
    ]);

    expect(registry.cards.map((c) => c.title)).toEqual(['宫格甲', '乙模块']);
    expect(registry.cards[0].icon).toBe(Layers);
  });

  it('收集所有声明过的权限，并挂到对应路由上', () => {
    const registry = createRegistry([
      moduleOf({
        id: 'a',
        routes: [
          {
            path: 'a',
            load,
            permissions: ['user:manage'],
            nav: { label: 'A', icon: Boxes, group: '系统' },
          },
        ],
        slots: [{ name: 'topbar.actions', component: () => null, permissions: ['user:read'] }],
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

  /* 旧写法里「导航 / 卡片指向不存在的路由」要靠注册期比对字符串来抓；
     派生之后 path 就是 route.path，这类错误从结构上不可能发生。
     下面两条改为守住派生本身，以及新引入的结构约束。 */
  it('导航项的 path 恒等于所属路由，不存在「幽灵导航」', () => {
    const registry = createRegistry([
      moduleOf({
        id: 'a',
        routes: [
          {
            path: 'real',
            load,
            nav: { label: '真实', icon: Boxes, group: '系统' },
          },
        ],
      }),
    ]);

    expect(registry.navGroups[0].items[0].path).toBe(
      registry.routes[0].path,
    );
  });

  it('bare 路由不能声明 nav / card（裸页放进侧栏或宫格会丢外壳）', () => {
    expect(() =>
      createRegistry([
        moduleOf({
          id: 'a',
          routes: [
            {
              path: 'login',
              layout: 'bare',
              load,
              card: { subtitle: 's', gradient: 'x', icon: Boxes },
            },
          ],
        }),
      ]),
    ).toThrow(/独立页面 login 不能声明 nav \/ card/);
  });

  it('card 既没 icon 也没有 nav.icon → 抛错（产物类型不允许缺图标）', () => {
    expect(() =>
      createRegistry([
        moduleOf({
          id: 'a',
          routes: [{ path: 'a', load, card: { subtitle: 's', gradient: 'x' } }],
        }),
      ]),
    ).toThrow(/缺少 icon/);
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
