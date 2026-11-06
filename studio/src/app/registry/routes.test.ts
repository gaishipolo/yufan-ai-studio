import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { renderRoutes } from '@/shared/registry/render-routes';
import { appRegistry } from './index';

/* 放在 app/registry 下：shared 层不允许 import app 层（no-restricted-imports）。 */

describe('路由清单', () => {
  it('docs/ROUTES.md 与注册表一致', () => {
    let onDisk: string;
    try {
      onDisk = readFileSync(join(process.cwd(), '../docs/ROUTES.md'), 'utf8');
    } catch {
      // 明确失败，不要 skip：单独检出 studio 时静默跳过，CI 上就是「绿了等于没跑」
      throw new Error('缺少 docs/ROUTES.md，请运行 `npm run gen:routes`');
    }
    expect(onDisk).toBe(renderRoutes(appRegistry));
  });

  it('渲染结果覆盖全部路由与导航分组', () => {
    const markdown = renderRoutes(appRegistry);
    expect(appRegistry.routes.length).toBeGreaterThan(0);
    for (const route of appRegistry.routes) {
      expect(markdown, `清单里漏了 ${route.path}`).toContain(`\`${route.path}\``);
    }
    for (const group of appRegistry.navGroups) {
      expect(markdown, `清单里漏了分组 ${group.title}`).toContain(group.title);
    }
  });
});
