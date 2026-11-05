import { db } from '../db';
import type { MockRoute } from '../kernel';

/* ---------------- 资产 / 模板 / 分镜 / 镜头 / 生图记录 ---------------- */

export const routes: MockRoute[] = [
  {
    method: 'GET',
    pattern: /^\/assets$/,
    handler: (ctx) => {
      const type = String(ctx.params.type ?? 'all');
      const kw = String(ctx.params.keyword ?? '').trim().toLowerCase();
      let list = db.assets.slice();
      if (type !== 'all') list = list.filter((a) => a.type === type);
      if (kw) list = list.filter((a) => a.name.toLowerCase().includes(kw));
      return {
        items: list,
        total: list.length,
        counts: {
          all: db.assets.length,
          image: db.assets.filter((a) => a.type === 'image').length,
          video: db.assets.filter((a) => a.type === 'video').length,
          audio: db.assets.filter((a) => a.type === 'audio').length,
        },
      };
    },
  },
  {
    method: 'GET',
    pattern: /^\/templates$/,
    handler: () => ({ items: db.templates, total: db.templates.length }),
  },
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
    handler: () => ({ items: db.imageJobs, total: db.imageJobs.length }),
  },
];
