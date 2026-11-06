import type { AspectRatio } from '@/entities/task/model/types';
import { db, type MockImageJob } from '../db';
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
      const modelId = ctx.body.model ? String(ctx.body.model) : 'agnes-image-2.5-flash';
      const job: MockImageJob = {
        id: `IMG${Date.now()}`,
        name: prompt.slice(0, 12),
        prompt,
        size: String(ctx.body.size ?? '1024x1024'),
        count: Number(ctx.body.count ?? 1),
        status: 'queued',
        progress: 0,
        model: modelId === 'seedream-3' ? 'Seedream 3' : 'Agnes Image 2.5 Flash',
        modelId,
        // 真实后端成图前 coverUrl 是空串：mock 也留空，占位图与轮询效果才一致
        coverUrl: '',
        outputUrl: null,
        errorMessage: null,
        createdAt: new Date().toISOString(),
      };
      db.imageJobs.unshift(job);
      return job;
    },
    status: 201,
  },
];
