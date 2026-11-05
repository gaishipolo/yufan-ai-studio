import type { MockRoute } from '@/mocks/kernel';

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
 *     pattern: /^\/usage$/,
 *     handler: (ctx) => ({ items: [], total: 0, page: Number(ctx.params.page ?? 1) }),
 *   },
 * ];
 */
export const routes: MockRoute[] = [];
