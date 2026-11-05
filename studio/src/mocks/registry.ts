import type { MockRoute } from './kernel';

/* ------------------------------------------------------------------
 * Mock 路由注册表
 * 各业务 mock 通过 registerMockRoutes 注册，adapter 只负责分发。
 * 因此新增接口 mock 不需要改 adapter.ts。
 * ------------------------------------------------------------------ */

const routes: MockRoute[] = [];

export function registerMockRoutes(list: MockRoute[]) {
  routes.push(...list);
}

/** 按注册顺序返回第一条命中的路由 */
export function resolveMockRoute(method: string, path: string): MockRoute | undefined {
  return routes.find((route) => route.method === method && route.pattern.test(path));
}

/** 调试用：当前已注册的路由数量 / 列表 */
export function mockRouteTable(): ReadonlyArray<{ method: string; pattern: string }> {
  return routes.map((route) => ({ method: route.method, pattern: route.pattern.source }));
}
