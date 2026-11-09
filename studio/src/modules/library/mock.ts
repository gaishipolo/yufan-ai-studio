import { ok, type MockRoute } from '@/mocks/kernel';

/**
 * 资产库 v2 · 骨架 mock（M0）。
 *
 * 正则**精确锚定** `/library/...`：模块级 mock 按字典序注册在骨架 mock 之前，
 * 写成 `/assets` 或过宽的前缀会抢走现役素材库页面的请求——那是并存期最隐蔽的
 * 一类回归（旧页面拿到 v2 的空数据，看起来像「数据丢了」）。
 *
 * M0 只覆盖两条只读接口；上传、共享、删除、下载等写接口随 M2/M3 的
 * 实现一起补，不提前造一批「永远成功」的假 handler。
 */
const EMPTY_FACETS = { all: 0, personal: 0, group: 0, team: 0, drafts: 0, trash: 0 };

export const routes: MockRoute[] = [
  {
    method: 'GET',
    pattern: /^\/library\/assets$/,
    handler: (ctx) => {
      const page = Math.max(1, Number(ctx.params.page ?? 1) || 1);
      const pageSize = Math.max(1, Number(ctx.params.pageSize ?? 24) || 24);
      return ok({ items: [], total: 0, page, pageSize, facets: EMPTY_FACETS });
    },
  },
  {
    method: 'GET',
    pattern: /^\/library\/facets$/,
    handler: () => ok(EMPTY_FACETS),
  },
];
