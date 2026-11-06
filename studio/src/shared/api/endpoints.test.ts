import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

/* ------------------------------------------------------------------
 * C3 · 契约不只到类型，也要到路径
 *
 * 后端路径全集取自生成物 `schema.ts` 的 `EndpointData` 键，而不是直接读
 * `api/docs/openapi.json`：前端单独检出时后者不存在，旧做法只能 skip 掉守卫；
 * 生成物由后端 `export-openapi --check` 字节比对保证最新，这里读它即可。
 *
 * 前端路径取自 `entities/**\/*.api.ts` 与旁路通道 `shared/api/auth.ts` 里的
 * **字面量**。覆盖范围有边界，写在这里免得被当成全覆盖：
 *   - `createRestResource(base)` 在工厂内部拼出的 detail 路径，由工厂自己的单测覆盖；
 *   - 运行时才拼出来的 URL（如 `archive.api.ts` 里 `url.replace(...)`）不在此列。
 * 除此之外，如果出现「解析不了的路径模板」，下面会直接红，不允许静默绕过。
 * ------------------------------------------------------------------ */

const STUDIO_ROOT = process.cwd();
const API_PREFIX = '/api/v1';

/** 生成物里的键形如 `"GET /api/v1/tasks/{task_id}": Page<VideoTask>;` */
const ENDPOINT_KEY = /^\s*"[A-Z]+ (\/api\/v1\/[^"]+)":/gm;
const STRING_LITERAL = /['"`]([^'"`]*)['"`]/g;
const BASE_CONST = /const\s+(\w+)\s*=\s*['"`](\/[^'"`]*)['"`]/g;

function stripComments(source: string): string {
  // api 层里没有含 `//` 的 URL 字面量，简单剥离即可；真出现了会表现为测试红，不会误绿
  return source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\n]*/g, '');
}

function normalize(path: string): string {
  const withoutQuery = path.replace(/\?.*$/, '').replace(/\/+$/, '');
  const full = withoutQuery.startsWith(API_PREFIX) ? withoutQuery : `${API_PREFIX}${withoutQuery}`;
  return full.replace(/\{[^}]+\}/g, '{}');
}

function backendPaths(): Set<string> {
  const schema = readFileSync(join(STUDIO_ROOT, 'src/shared/api/schema.ts'), 'utf8');
  const paths = new Set<string>();
  for (const match of schema.matchAll(ENDPOINT_KEY)) paths.add(normalize(match[1]));
  return paths;
}

function collectApiFiles(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) collectApiFiles(full, out);
    else if (entry.isFile() && full.endsWith('.api.ts') && !full.endsWith('.test.ts')) out.push(full);
  }
  return out;
}

interface ScanResult {
  used: Map<string, string[]>;
  /** 以变量开头、又查不到同文件 const 字面量的路径模板：必须让人看见，不能默默漏掉 */
  unresolved: string[];
}

function scanFrontendPaths(): ScanResult {
  const files = [
    ...collectApiFiles(join(STUDIO_ROOT, 'src/entities')),
    join(STUDIO_ROOT, 'src/shared/api/auth.ts'),
  ];
  const used = new Map<string, string[]>();
  const unresolved: string[] = [];

  for (const file of files) {
    const source = stripComments(readFileSync(file, 'utf8'));
    const bases = new Map<string, string>();
    for (const match of source.matchAll(BASE_CONST)) bases.set(match[1], match[2]);

    for (const match of source.matchAll(STRING_LITERAL)) {
      let raw = match[1];
      // `${base}/...` → `/identity/roles/...`；其它 `${id}` → `{}` 占位
      raw = raw.replace(/\$\{(\w+)\}/g, (_whole, name) => bases.get(name) ?? '{}');
      raw = raw.replace(/\$\{[^}]*\}/g, '{}');

      if (!raw.startsWith('/')) {
        // 模板变量开头且没解析出来 = 这条路径不在守卫覆盖内，明确报错
        if (/^\{\}/.test(raw) && raw.includes('/')) unresolved.push(`${file}: ${match[1]}`);
        continue;
      }
      if (raw === '/' || raw === API_PREFIX) continue; // 前缀本身与根路径不是端点

      const path = normalize(raw);
      used.set(path, [...(used.get(path) ?? []), file.replace(STUDIO_ROOT, '')]);
    }
  }

  return { used, unresolved };
}

describe('前端接口路径契约', () => {
  it('生成物里确实抽出了后端端点（防止正则失效导致空集假绿）', () => {
    expect(backendPaths().size).toBeGreaterThan(30);
  });

  it('api 层里确实抽出了前端路径', () => {
    expect(scanFrontendPaths().used.size).toBeGreaterThan(20);
  });

  it('没有解析不了的路径模板', () => {
    expect(scanFrontendPaths().unresolved).toEqual([]);
  });

  it('每条 api 层路径都在后端 EndpointData 里', () => {
    const backend = backendPaths();
    const missing = [...scanFrontendPaths().used.entries()]
      .filter(([path]) => !backend.has(path))
      .map(([path, files]) => `${path}  <- ${files.join(', ')}`);
    expect(missing, `以下路径在生成物里不存在，检查拼写或让后端重新导出：\n${missing.join('\n')}`).toEqual([]);
  });
});
