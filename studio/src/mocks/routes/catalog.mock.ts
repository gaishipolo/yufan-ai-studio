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
    // 批量删除（与后端 `DELETE /images` 同形）：不存在或本次已删过的进 skipped。
    // 以前 mock 里没有这条，任务中心在 mock 模式下勾了生图点删除会直接 404
    // —— 新写的界面看着像坏的（2026-11-15 审查 P3）。
    method: 'DELETE',
    pattern: /^\/images$/,
    handler: (ctx) => {
      const ids = Array.isArray(ctx.body.ids) ? ctx.body.ids.map(String) : [];
      const skipped: string[] = [];
      let deleted = 0;
      for (const id of ids) {
        const index = db.imageJobs.findIndex((item) => item.id === id);
        if (index < 0) {
          skipped.push(id);
          continue;
        }
        db.imageJobs.splice(index, 1);
        deleted += 1;
      }
      return {deleted, skipped};
    },
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
