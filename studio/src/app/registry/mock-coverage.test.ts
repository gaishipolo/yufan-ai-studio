import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { mockRouteTable } from '@/mocks/registry';
import '@/mocks/routes';
import { parseEndpoints, renderMockCoverage } from '@/shared/registry/render-mock-coverage';

/* 放在 app/registry 下与 routes.test.ts 同处：两者都是「生成物与源码一致」的守卫。
 *
 * 只守「文件与当前 mock 表一致」，**不守覆盖率**（2026-11-06 用户拍板）：
 * 一条 handler 能匹配多个端点，「每个端点都要有 mock」无法可靠机械判定，
 * 硬做成断言只会变成可绕过的假守卫。
 *
 * 但「不作门禁」不等于「允许静默过期」：MOCK_COVERAGE.md 里的未命中清单
 * 会被当成待办读，过期就会误导，所以至少要跟上源码。
 *
 * 渲染走 `render-mock-coverage.ts` —— 与生成脚本同一份实现，测试不再自己拼一遍，
 * 也就不用起子进程去跑脚本（那样会让整个测试集多背 1.7s 的并行负载）。
 */
describe('mock 覆盖率清单', () => {
  it('docs/MOCK_COVERAGE.md 与当前 mock 路由表一致', () => {
    const cwd = process.cwd();

    let onDisk: string;
    try {
      onDisk = readFileSync(join(cwd, '../docs/MOCK_COVERAGE.md'), 'utf8');
    } catch {
      // 明确失败，不要 skip：单独检出 studio 时静默跳过，CI 上就是「绿了等于没跑」
      throw new Error('缺少 docs/MOCK_COVERAGE.md，请运行 `npm run gen:mock-coverage`');
    }

    const endpoints = parseEndpoints(readFileSync(join(cwd, 'src/shared/api/schema.ts'), 'utf8'));
    const modules = readdirSync(join(cwd, 'src/modules'), { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .map((entry) => ({
        id: entry.name,
        hasMock: existsSync(join(cwd, 'src/modules', entry.name, 'mock.ts')),
      }))
      .sort((a, b) => a.id.localeCompare(b.id));

    expect(onDisk).toBe(renderMockCoverage({ endpoints, routes: mockRouteTable(), modules }));
  });
});
