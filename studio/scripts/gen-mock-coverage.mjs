/* 生成 docs/MOCK_COVERAGE.md —— 只记录覆盖率，不做门禁。
 *
 * 口径（2026-11-06 用户拍板）：一条 mock handler 可能覆盖多个端点，
 * 「每个端点都必须有 mock」无法可靠机械判定，硬做成 CI 断言只会变成
 * 可绕过的假守卫。所以这里只把现状列清楚，不要求任何数字达标。
 *
 * 后端端点取自生成物 schema.ts 的 EndpointData 键；mock 路由取自真实注册表
 * （用 Vite SSR 执行自动发现入口，不手写正则去猜 mock 文件）。
 *
 *   node scripts/gen-mock-coverage.mjs           写盘
 *   node scripts/gen-mock-coverage.mjs --check   只比对，不写
 */
import { existsSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { createServer } from 'vite';

const root = process.cwd();
const target = resolve(root, '../docs/MOCK_COVERAGE.md');
const check = process.argv.includes('--check');
const PREFIX = '/api/v1';

/* ---------------- 后端端点 ---------------- */
const schema = readFileSync(join(root, 'src/shared/api/schema.ts'), 'utf8');
const endpoints = [];
for (const match of schema.matchAll(/^\s*"([A-Z]+) (\/api\/v1\/[^"]+)":/gm)) {
  endpoints.push({ method: match[1], path: match[2] });
}
endpoints.sort((a, b) => a.path.localeCompare(b.path) || a.method.localeCompare(b.method));

/* ---------------- 真实 mock 路由表 ---------------- */
const server = await createServer({
  root,
  appType: 'custom',
  logLevel: 'error',
  server: { middlewareMode: true },
});
let table;
try {
  await server.ssrLoadModule('/src/mocks/routes/index.ts'); // 触发自动发现 + 注册
  const registry = await server.ssrLoadModule('/src/mocks/registry.ts');
  table = registry.mockRouteTable();
} finally {
  await server.close();
}

/** pathOf() 会剥掉 /api/v1 前缀；{param} 用一个样例值代入才能被正则命中 */
function mockPathOf(path) {
  const stripped = path.startsWith(PREFIX) ? path.slice(PREFIX.length) || '/' : path;
  return stripped.replace(/\{[^}]+\}/g, 'demo');
}

const matchers = table.map((route) => ({
  method: route.method,
  regexp: new RegExp(route.pattern),
}));
function hit(endpoint) {
  const path = mockPathOf(endpoint.path);
  return matchers.some((m) => m.method === endpoint.method && m.regexp.test(path));
}

const covered = endpoints.filter(hit);
const missing = endpoints.filter((endpoint) => !hit(endpoint));

/* ---------------- 模块级覆盖率 ---------------- */
const modules = readdirSync(join(root, 'src/modules'), { withFileTypes: true })
  .filter((entry) => entry.isDirectory())
  .map((entry) => entry.name)
  .sort();
const withMock = modules.filter((id) =>
  existsSync(join(root, 'src/modules', id, 'mock.ts')),
);

/* ---------------- 渲染 ---------------- */
const lines = [
  '<!-- 由 `npm run gen:mock-coverage` 生成，请勿手工编辑。 -->',
  '',
  '# Mock 覆盖率（只记录，不作门禁）',
  '',
  `后端端点 ${endpoints.length} 条（来自 \`schema.ts\` 的 \`EndpointData\`），`,
  `已注册 mock 路由 ${table.length} 条（来自 \`mockRouteTable()\`），`,
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
  ...modules.map((id) => `| \`${id}\` | ${withMock.includes(id) ? '✓' : '—'} |`),
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
const content = lines.join('\n');

if (check) {
  const onDisk = existsSync(target) ? readFileSync(target, 'utf8') : '';
  if (onDisk !== content) {
    console.error('docs/MOCK_COVERAGE.md 与源码不一致 —— 运行 `npm run gen:mock-coverage`');
    process.exitCode = 1;
  } else {
    console.log('docs/MOCK_COVERAGE.md 已同步');
  }
} else {
  writeFileSync(target, content, 'utf8');
  console.log(
    `已生成 ${target}（模块 ${withMock.length}/${modules.length}，端点疑似覆盖 ${covered.length}/${endpoints.length}）`,
  );
}
