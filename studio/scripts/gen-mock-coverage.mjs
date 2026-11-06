/* 生成 docs/MOCK_COVERAGE.md —— 只记录覆盖率，不做门禁。
 *
 * 口径（2026-11-06 用户拍板）：一条 mock handler 可能覆盖多个端点，
 * 「每个端点都必须有 mock」无法可靠机械判定，硬做成 CI 断言只会变成
 * 可绕过的假守卫。所以这里只把现状列清楚，不要求任何数字达标。
 *
 * 渲染逻辑不在这里：它住在 `src/shared/registry/render-mock-coverage.ts`，
 * 由本脚本与 `src/app/registry/mock-coverage.test.ts` 共用 ——
 * 两边各拼一份字符串的话，两边同时写错仍然会「比对通过」。
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

/* 用 Vite SSR 真实执行自动发现入口，不手写正则去猜哪些文件是 mock */
const server = await createServer({
  root,
  appType: 'custom',
  logLevel: 'error',
  server: { middlewareMode: true },
});

let content;
try {
  await server.ssrLoadModule('/src/mocks/routes/index.ts'); // 触发各模块 mock 注册
  const { mockRouteTable } = await server.ssrLoadModule('/src/mocks/registry.ts');
  const { parseEndpoints, renderMockCoverage } = await server.ssrLoadModule(
    '/src/shared/registry/render-mock-coverage.ts',
  );

  const endpoints = parseEndpoints(readFileSync(join(root, 'src/shared/api/schema.ts'), 'utf8'));
  const routes = mockRouteTable();
  const modules = readdirSync(join(root, 'src/modules'), { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => ({
      id: entry.name,
      hasMock: existsSync(join(root, 'src/modules', entry.name, 'mock.ts')),
    }))
    .sort((a, b) => a.id.localeCompare(b.id));

  content = renderMockCoverage({ endpoints, routes, modules });
} finally {
  await server.close();
}

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
  const summary = content.match(/\*\*(\d+) \/ (\d+)\*\*/);
  const covered = content.match(/判定疑似覆盖 (\d+) 条/);
  console.log(
    `已生成 ${target}（模块 ${summary?.[1] ?? '?'}/${summary?.[2] ?? '?'}，端点疑似覆盖 ${
      covered?.[1] ?? '?'
    }）`,
  );
}
