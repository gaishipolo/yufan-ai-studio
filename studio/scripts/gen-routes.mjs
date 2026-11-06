/* 从真实注册表生成 docs/ROUTES.md。
 *
 * 用 Vite 的 SSR 加载器执行 `src/app/registry/index.ts`，让 `import.meta.glob`
 * 自己发现模块——不用正则去"猜" module.ts 的内容，也不新增依赖。
 *
 *   node scripts/gen-routes.mjs           写盘
 *   node scripts/gen-routes.mjs --check   只比对，不写（CI 用）
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { createServer } from 'vite';

const root = process.cwd();
const target = resolve(root, '../docs/ROUTES.md');
const check = process.argv.includes('--check');

const server = await createServer({
  root,
  appType: 'custom',
  logLevel: 'error',
  server: { middlewareMode: true },
});

try {
  const { appRegistry } = await server.ssrLoadModule('/src/app/registry/index.ts');
  const { renderRoutes } = await server.ssrLoadModule('/src/shared/registry/render-routes.ts');
  const content = renderRoutes(appRegistry);

  if (check) {
    let onDisk = '';
    try {
      onDisk = readFileSync(target, 'utf8');
    } catch {
      onDisk = '';
    }
    if (onDisk !== content) {
      console.error('docs/ROUTES.md 与注册表不一致 —— 运行 `npm run gen:routes`');
      process.exitCode = 1;
    } else {
      console.log(`docs/ROUTES.md 已同步（${appRegistry.routes.length} 条路由）`);
    }
  } else {
    writeFileSync(target, content, 'utf8');
    console.log(`已生成 ${target}（${appRegistry.routes.length} 条路由 / ${appRegistry.modules.length} 个模块）`);
  }
} finally {
  await server.close();
}
