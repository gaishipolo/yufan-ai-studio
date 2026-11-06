import { db } from '../db';
import { HttpError, type MockRoute } from '../kernel';
import type { Asset } from '@/entities/asset/model/types';

/* ---------------- 资产 / 模板 / 分镜 / 镜头 / 生图记录 ---------------- */

const assetPage = (params: Record<string, unknown>) => {
  const page = Math.max(1, Number(params.page ?? 1) || 1);
  const pageSize = Math.max(1, Number(params.pageSize ?? 24) || 24);
  return { page, pageSize, offset: (page - 1) * pageSize };
};

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
      // 分面计数按「除类型以外的筛选条件」统计，与后端一致
      const scoped = kw ? db.assets.filter((a) => a.name.toLowerCase().includes(kw)) : db.assets;
      const { page, pageSize, offset } = assetPage(ctx.params);
      return {
        items: list.slice(offset, offset + pageSize),
        total: list.length,
        page,
        pageSize,
        counts: {
          all: scoped.length,
          image: scoped.filter((a) => a.type === 'image').length,
          video: scoped.filter((a) => a.type === 'video').length,
          audio: scoped.filter((a) => a.type === 'audio').length,
        },
      };
    },
  },
  {
    method: 'POST',
    pattern: /^\/assets\/upload$/,
    status: 201,
    handler: (ctx) => {
      const file = ctx.body as unknown as File;
      const name = String(ctx.params.filename ?? '未命名素材');
      const mimeType = String(file?.type || 'application/octet-stream');
      const type: Asset['type'] = mimeType.startsWith('video/')
        ? 'video'
        : mimeType.startsWith('audio/')
          ? 'audio'
          : 'image';
      const asset: Asset = {
        id: `A${Date.now()}`,
        name,
        type,
        mimeType,
        sizeBytes: typeof file?.size === 'number' ? file.size : 0,
        width: 0,
        height: 0,
        duration: 0,
        hasAudio: type !== 'image',
        category: null,
        source: 'upload',
        coverUrl: '',
        fileUrl: '',
        refCount: 0,
        createdAt: new Date().toISOString(),
      };
      db.assets.unshift(asset);
      return asset;
    },
  },
  {
    method: 'DELETE',
    pattern: /^\/assets\/[^/]+$/,
    handler: (ctx) => {
      const id = ctx.path.split('/')[2];
      const index = db.assets.findIndex((a) => a.id === id);
      if (index < 0) throw new HttpError(404, '素材不存在');
      // 被引用的素材不许删：与后端 409 的行为对齐，否则演示时删得掉、真实环境删不掉
      if (db.assets[index].refCount > 0) throw new HttpError(409, '该素材仍被任务或模板引用，无法删除');
      db.assets.splice(index, 1);
      return null;
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
