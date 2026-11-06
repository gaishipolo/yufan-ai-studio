import type { AppRegistry } from './types';

/* ------------------------------------------------------------------
 * 路由清单的渲染逻辑放在这里，而不是塞进生成脚本或测试里：
 * 生成脚本（写盘）与守卫测试（字节比对）必须共用同一份渲染，
 * 否则「两边都写错了但比对仍绿」这种假通过就没人发现。
 *
 * 不写生成时间：`--check` 靠字节比对，带时间戳会每天误报。
 * ------------------------------------------------------------------ */

const BANNER =
  '<!-- 由 `npm run gen:routes` 生成，请勿手工编辑。来源：studio/src/modules/*/module.ts -->';

function cell(value: string): string {
  return value.replace(/\|/g, '\\|').replace(/\n/g, ' ');
}

function code(value: string): string {
  return `\`${cell(value)}\``;
}

export function renderRoutes(registry: AppRegistry): string {
  const navLabels = new Map<string, string>();
  for (const group of registry.navGroups) {
    for (const item of group.items) {
      navLabels.set(item.path, `${group.title} / ${item.label}`);
    }
  }
  const cardPaths = new Set(registry.cards.map((card) => card.path));

  const lines: string[] = [
    BANNER,
    '',
    '# 前端路由清单',
    '',
    `共 ${registry.routes.length} 条路由，来自 ${registry.modules.length} 个模块。`,
    '',
    '> 路由由 `src/app/registry/index.ts` 的 `import.meta.glob` 自动发现；',
    '> 新增模块只需写 `module.ts`，本文件由 `npm run gen:routes` 重新生成。',
    '',
    '| 路径 | 模块 | 外壳 | 权限 | 侧栏 | 宫格 |',
    '| --- | --- | --- | --- | --- | --- |',
  ];

  for (const route of [...registry.routes].sort((a, b) => a.path.localeCompare(b.path))) {
    const permissions = route.permissions.length
      ? route.permissions.map(code).join(', ')
      : '—';
    const nav = navLabels.get(route.path);
    lines.push(
      `| ${code(route.path)} | ${cell(route.moduleId)} | ${route.layout} | ${permissions} | ${nav ? cell(nav) : '—'} | ${cardPaths.has(route.path) ? '✓' : '—'} |`,
    );
  }

  lines.push('', '## 导航分组', '');
  for (const group of registry.navGroups) {
    lines.push(`- **${cell(group.title)}**（order ${group.order}）`);
    for (const item of group.items) {
      lines.push(`  - ${cell(item.label)} → ${code(item.path)}`);
    }
  }

  lines.push('', '## 模块清单', '', '| 模块 | 路由 | 导航 | 卡片 | 插槽 |', '| --- | --- | --- | --- | --- |');
  for (const mod of [...registry.modules].sort((a, b) => a.id.localeCompare(b.id))) {
    lines.push(
      `| ${cell(mod.id)} | ${mod.routes.length} | ${mod.routes.filter((r) => r.nav).length} | ${mod.routes.filter((r) => r.card).length} | ${(mod.slots ?? []).length} |`,
    );
  }

  lines.push('');
  return lines.join('\n');
}
