import type { AspectRatio } from '@/entities/task/model/types';
import { db } from '../db';
import { HttpError, type MockRoute } from '../kernel';

/* ---------------- 生成（同步返回任务 / 生图记录） ---------------- */

export const routes: MockRoute[] = [
  {
    method: 'POST',
    pattern: /^\/generations\/video$/,
    handler: (ctx) => {
      const prompt = String(ctx.body.prompt ?? '').trim();
      if (!prompt) throw new HttpError(400, '画面描述不能为空');
      return db.addTask({
        title: String(ctx.body.title ?? prompt.slice(0, 24)),
        prompt,
        model: String(ctx.body.model ?? 'Seedance 2.5'),
        durationSec: Number(ctx.body.durationSec ?? 15),
        aspectRatio: (ctx.body.aspectRatio as AspectRatio) ?? '9:16',
      });
    },
    status: 201,
  },
  {
    method: 'POST',
    pattern: /^\/generations\/image$/,
    handler: (ctx) => {
      const prompt = String(ctx.body.prompt ?? '').trim();
      if (!prompt) throw new HttpError(400, '提示词不能为空');
      const seq = db.imageJobs.length + 1;
      const job = {
        id: `IMG${Date.now()}`,
        name: prompt.slice(0, 12),
        prompt,
        size: String(ctx.body.size ?? '1024×1536'),
        coverUrl: `https://picsum.photos/seed/sf-img-new-${seq}/400/500`,
        createdAt: new Date().toISOString(),
      };
      db.imageJobs.unshift(job);
      return job;
    },
    status: 201,
  },
];
