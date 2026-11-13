import { db } from '../db';
import type { MockRoute } from '../kernel';

/* ---------------- 分镜 / 镜头 / 生图记录 ----------------
 *
 * 素材（`/assets`）那一整套 mock 随 v1 素材库于 2026-11-13 下线一起删了。
 * 资产库只剩 v2，mock 在 `modules/library/mock.ts` 里，路径前缀是 `/library`。
 */

export const routes: MockRoute[] = [
  {
    method: 'GET',
    pattern: /^\/storyboards$/,
    handler: () => ({ items: db.storyboards, total: db.storyboards.length }),
  },
  {
    method: 'GET',
    pattern: /^\/shots$/,
    handler: () => ({ items: db.shots, total: db.shots.length }),
  },
  {
    method: 'GET',
    pattern: /^\/images$/,
    handler: (ctx) => {
      // 每次拉取推进一次后台 worker，mock 模式也能看到 queued → succeeded
      db.tick();
      return {
        items: db.imageJobs,
        total: db.imageJobs.length,
        page: Number(ctx.params.page ?? 1),
        pageSize: Number(ctx.params.pageSize ?? 24),
      };
    },
  },
];
