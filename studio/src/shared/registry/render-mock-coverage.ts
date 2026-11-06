/* `docs/MOCK_COVERAGE.md` 的唯一渲染实现。
 *
 * 生成脚本（`scripts/gen-mock-coverage.mjs`）与守卫测试
 * （`app/registry/mock-coverage.test.ts`）共用它 —— 两个实现各拼一份字符串时，
 * 两边都写错仍然会「比对通过」。这和 `render-routes.ts` 是同一个取舍。
 *
 * 只记录、不做门禁：这里不判断覆盖率是否达标，只负责把现状如实写出来。
 */

export interface CoverageEndpoint {
  method: string;
  path: string;
}

/** `mockRouteTable()` 的返回形状（正则的 source 字符串） */
export interface MockRouteSummary {
  method: string;
  pattern: string;
}

export interface ModuleCoverage {
  id: string;
  hasMock: boolean;
}

export interface MockCoverageInput {
  /* 只读：`mockRouteTable()` 返回的就是 ReadonlyArray，这里不复制也不改动入参 */
  endpoints: ReadonlyArray<CoverageEndpoint>;
  routes: ReadonlyArray<MockRouteSummary>;
  modules: ReadonlyArray<ModuleCoverage>;
}

const PREFIX = '/api/v1';

/** 从生成物 `schema.ts` 的 `EndpointData` 键里取后端端点全集 */
export function parseEndpoints(schemaText: string): CoverageEndpoint[] {
  const endpoints: CoverageEndpoint[] = [];
  for (const match of schemaText.matchAll(/^\s*"([A-Z]+) (\/api\/v1\/[^"]+)":/gm)) {
    endpoints.push({ method: match[1], path: match[2] });
  }
  endpoints.sort((a, b) => a.path.localeCompare(b.path) || a.method.localeCompare(b.method));
  return endpoints;
}

/** `pathOf()` 会剥掉 `/api/v1` 前缀；`{param}` 代入样例值才能被正则命中 */
function mockPathOf(path: string): string {
  const stripped = path.startsWith(PREFIX) ? path.slice(PREFIX.length) || '/' : path;
  return stripped.replace(/\{[^}]+\}/g, 'demo');
}

export function renderMockCoverage({ endpoints, routes, modules }: MockCoverageInput): string {
  const matchers = routes.map((route) => ({
    method: route.method,
    regexp: new RegExp(route.pattern),
  }));

  const isCovered = (endpoint: CoverageEndpoint) => {
    const path = mockPathOf(endpoint.path);
    return matchers.some((m) => m.method === endpoint.method && m.regexp.test(path));
  };

  const covered = endpoints.filter(isCovered);
  const missing = endpoints.filter((endpoint) => !isCovered(endpoint));
  const withMock = modules.filter((module) => module.hasMock);

  const lines = [
    '<!-- 由 `npm run gen:mock-coverage` 生成，请勿手工编辑。 -->',
    '',
    '# Mock 覆盖率（只记录，不作门禁）',
    '',
    `后端端点 ${endpoints.length} 条（来自 \`schema.ts\` 的 \`EndpointData\`），`,
    `已注册 mock 路由 ${routes.length} 条（来自 \`mockRouteTable()\`），`,
    `按「同方法 + 正则能匹配」判定疑似覆盖 ${covered.length} 条。`,
    '',
    '> **这是记录，不是门禁**：CI 不检查覆盖率是否达标，也不要求新增端点必须带 mock。',
    '> 一条 handler 可以覆盖多个端点（例如 `/tasks/([\\w-]+)` 同时命中详情与删除），',
    '> 「每个端点都有 mock」无法可靠机械判定；硬做成断言只会变成可绕过的假守卫。',
    '> 判定本身也是启发式的：路径参数代入样例值 `demo`，只求「正则能否命中」，',
    '> 不代表 handler 真的返回了正确数据。改了接口或 mock 后，重跑一次本脚本即可。',
    '',
    '## 模块级覆盖率',
    '',
    `**${withMock.length} / ${modules.length}** 个模块带 \`mock.ts\`。`,
    '',
    '| 模块 | mock.ts |',
    '| --- | --- |',
    ...modules.map((module) => `| \`${module.id}\` | ${module.hasMock ? '✓' : '—'} |`),
    '',
    '## 未命中端点的清单',
    '',
    '以下端点在当前注册表里找不到同方法的匹配路由。它们**不一定**是缺陷——',
    '有些接口只在真实后端使用（如健康检查、静态资源、归档导入），mock 模式从来不会调到。',
    '',
    '| 方法 | 路径 |',
    '| --- | --- |',
    ...missing.map((e) => `| ${e.method} | \`${e.path.replace(/\{[^}]+\}/g, '{}')}\` |`),
    '',
    `（未命中 ${missing.length} 条；已命中 ${covered.length} 条不在本表重复列出。）`,
    '',
  ];
  return lines.join('\n');
}
