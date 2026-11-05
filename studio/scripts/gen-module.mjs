#!/usr/bin/env node
/**
 * 新功能脚手架：生成一个自包含的模块切片
 *
 *   npm run gen:module -- user-access "用户权限" "系统管理"
 *   npm run gen:module -- billing 计费                      # 分组默认「其他」
 *
 * 产物：
 *   src/modules/<id>/module.ts        模块清单（路由 / 导航 / 卡片 / 权限）
 *   src/modules/<id>/mock.ts          该功能专属 mock（默认空，附示例）
 *   src/pages/<id>/<Pascal>Page.tsx   页面占位
 *
 * 设计原则：只生成「声明 + 空壳」，不生成任何业务逻辑，避免脚手架与真实需求脱节。
 */

import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');

const ID_PATTERN = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/;

function die(message) {
  console.error(`\n✖ ${message}\n`);
  process.exit(1);
}

function pascalCase(id) {
  return id
    .split('-')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join('');
}

const [id, title = id, group = '其他'] = process.argv.slice(2);

if (!id) {
  die('缺少模块 id。用法：npm run gen:module -- user-access "用户权限" "系统管理"');
}
if (!ID_PATTERN.test(id)) {
  die(`模块 id 必须是 kebab-case（小写字母开头，可含数字与连字符）：${id}`);
}

const Pascal = pascalCase(id);
const files = {
  module: join(ROOT, 'src/modules', id, 'module.ts'),
  mock: join(ROOT, 'src/modules', id, 'mock.ts'),
  page: join(ROOT, 'src/pages', id, `${Pascal}Page.tsx`),
};

for (const [kind, file] of Object.entries(files)) {
  if (existsSync(file)) die(`${kind} 已存在，拒绝覆盖：${file}`);
}

const write = (file, content) => {
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, content, 'utf8');
};

write(
  files.module,
  `import { Boxes } from 'lucide-react';
import { defineModule } from '@/shared/registry/define-module';

/**
 * 模块清单：路由 / 侧边栏 / 顶部宫格卡片 / 插槽，一处声明，骨架自动装配。
 * 页面通过 load() 懒加载，不会进入首屏 bundle。
 */
export default defineModule({
  id: '${id}',
  title: '${title}',
  routes: [
    {
      path: '${id}',
      load: () => import('@/pages/${id}/${Pascal}Page'),
      // 需要门禁时打开下一行，并确认后端 / 登录接口会下发该权限点：
      // permissions: ['${id}:read'],
    },
  ],
  nav: [
    {
      label: '${title}',
      path: '/${id}',
      icon: Boxes,
      group: '${group}',
      groupOrder: 9,
      order: 1,
      // 导航项也会按权限自动隐藏：
      // permissions: ['${id}:read'],
    },
  ],
  // 需要出现在顶部宫格时打开（最多 4 个，order 决定位置）：
  // cards: [
  //   {
  //     title: '${title}',
  //     subtitle: '一句话说明',
  //     path: '/${id}',
  //     icon: Boxes,
  //     gradient: 'linear-gradient(140deg,#5b8cff,#7c5cff)',
  //     order: 5,
  //   },
  // ],
});
`,
);

write(
  files.page,
  `import { PageHeader } from '@/shared/ui';

export default function ${Pascal}Page() {
  return (
    <>
      <PageHeader title="${title}" description="TODO: 替换为真实功能" />
      <div className="mt-4 rounded-card border border-line-soft bg-panel-soft px-4 py-10 text-center text-xs text-ink-mute">
        页面已接入路由与导航，可以开始写业务了。
      </div>
    </>
  );
}
`,
);

write(
  files.mock,
  `import type { MockRoute } from '@/mocks/kernel';

/**
 * 本功能专属 mock：只影响 VITE_USE_MOCK=true 的开发环境，
 * 与其它同事的 mock 文件互不冲突。
 *
 * 示例（按需打开）：
 *
 * import { db } from '@/mocks/db';
 *
 * export const routes: MockRoute[] = [
 *   {
 *     method: 'GET',
 *     pattern: /^\\/${id}$/,
 *     handler: (ctx) => ({ items: [], total: 0, page: Number(ctx.params.page ?? 1) }),
 *   },
 * ];
 */
export const routes: MockRoute[] = [];
`,
);

console.log(`
✔ 模块 ${id} 已生成

  src/modules/${id}/module.ts
  src/modules/${id}/mock.ts
  src/pages/${id}/${Pascal}Page.tsx

下一步：
  1. npm run dev，访问 /${id} —— 路由与导航已自动生效
  2. 接口放 entities/${id}/api/*.api.ts，hook 放 *.queries.ts
  3. 需要权限时打开 module.ts 里的 permissions，并让后端下发对应权限点
  4. npm run verify 再提交
`);
