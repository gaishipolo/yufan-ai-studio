import type { AspectRatio, TaskStatus, VideoTask } from '@/entities/task/model/types';
import type { TaskFeedItem } from '@/entities/task/model/feed.types';
import { db, type MockImageJob } from '../db';
import { HttpError, type MockContext, type MockRoute } from '../kernel';

/* ---------------- 任务 ---------------- */

function listTasks(ctx: MockContext, maxPageSize = 100) {
  const {
    status = 'all',
    keyword = '',
    sort = 'createdAt_desc',
    scope = 'mine',
    onlyAuthorized = 'false',
    page = 1,
    pageSize = 24,
  } = ctx.params;

  // 模拟后台 worker 推进进度
  db.tick();

  let list = db.tasks.slice();

  if (scope === 'mine') {
    list = list.filter((t) => t.authorId === 'u_001');
  }
  if (String(onlyAuthorized) === 'true') {
    list = list.filter((t) => t.downloadable);
  }
  const kw = String(keyword).trim().toLowerCase();
  if (kw) {
    list = list.filter(
      (t) =>
        t.title.toLowerCase().includes(kw) ||
        t.model.toLowerCase().includes(kw) ||
        t.id.toLowerCase().includes(kw),
    );
  }

  const stats = {
    all: list.length,
    queued: list.filter((t) => t.status === 'queued').length,
    running: list.filter((t) => t.status === 'running').length,
    completed: list.filter((t) => t.status === 'completed').length,
    failed: list.filter((t) => t.status === 'failed').length,
  };

  if (status !== 'all') {
    list = list.filter((t) => t.status === (status as TaskStatus));
  }

  const compare: Record<string, (a: VideoTask, b: VideoTask) => number> = {
    createdAt_desc: (a, b) => (a.createdAt < b.createdAt ? 1 : -1),
    createdAt_asc: (a, b) => (a.createdAt > b.createdAt ? 1 : -1),
    title_asc: (a, b) => a.title.localeCompare(b.title, 'zh-CN'),
  };
  list.sort(compare[String(sort)] ?? compare.createdAt_desc);

  const p = Math.max(1, Number(page) || 1);
  const ps = Math.max(1, Math.min(maxPageSize, Number(pageSize) || 24));
  const total = list.length;

  return {
    items: list.slice((p - 1) * ps, p * ps),
    total,
    page: p,
    pageSize: ps,
    stats,
    // mock 账号即演示账号 u_001（老大），持有超级权限
    canReadAll: true,
  };
}

/** 生图状态 → 任务中心的统一分组：成功但没有成图不能算「已完成」 */
function imageGroup(job: MockImageJob): TaskFeedItem['group'] {
  if (job.status === 'queued' || job.status === 'running') return 'running';
  if (job.status === 'failed') return 'failed';
  return job.outputUrl ? 'completed' : 'attention';
}

export const routes: MockRoute[] = [
  {
    method: 'GET',
    pattern: /^\/tasks\/feed$/,
    handler: (ctx) => {
      // 演示库没有替换档案；明确提示，不把演示数据伪装成真实归档。
      db.tick();
      const source = String(ctx.params.source ?? 'all');
      const status = String(ctx.params.status ?? 'all');
      const keyword = String(ctx.params.keyword ?? '').trim().toLowerCase();
      const sort = String(ctx.params.sort ?? 'createdAt_desc');
      const onlyAuthorized = String(ctx.params.onlyAuthorized) === 'true';

      const data = listTasks({...ctx, params: {...ctx.params, status: 'all', page: 1, pageSize: Number.MAX_SAFE_INTEGER}}, Number.MAX_SAFE_INTEGER);
      const videoItems: TaskFeedItem[] = source === 'image' || source === 'replacement' ? [] : data.items.map(video => ({
        key: `video:${video.id}`, id: video.id, source: 'video', title: video.title,
        status: video.status, group: video.status === 'queued' ? 'running' : video.status,
        createdAt: video.createdAt, progress: video.progress, sourceName: '', video,
      }));

      const imageItems: TaskFeedItem[] = source === 'video' || source === 'replacement' ? [] : db.imageJobs
        .filter(job => !keyword || [job.name, job.prompt, job.model, job.id].some(value => String(value ?? '').toLowerCase().includes(keyword)))
        .filter(job => !onlyAuthorized || Boolean(job.outputUrl))
        .map((job) => {
          const group = imageGroup(job);
          const detailUrl = `/image?job=${job.id}`;
          return {
            key: `image:${job.id}`, id: job.id, source: 'image', title: job.name,
            status: job.status, group, createdAt: job.createdAt, progress: job.progress,
            sourceName: '', outputUrl: job.outputUrl, errorMessage: job.errorMessage, detailUrl,
            image: {
              id: job.id, title: job.name, status: job.status, group,
              createdAt: job.createdAt, progress: job.progress, model: job.model,
              modelId: job.modelId, size: job.size, count: job.count, prompt: job.prompt,
              coverUrl: job.coverUrl, outputUrl: job.outputUrl,
              errorMessage: job.errorMessage, externalId: job.externalId ?? null, detailUrl,
            },
          } satisfies TaskFeedItem;
        });

      const all = [...videoItems, ...imageItems];
      const stats = {
        all: all.length,
        running: all.filter(item => item.group === 'running').length,
        completed: all.filter(item => item.group === 'completed').length,
        failed: all.filter(item => item.group === 'failed').length,
        attention: all.filter(item => item.group === 'attention').length,
      };
      const filtered = all.filter(item => status === 'all' || item.group === status || (status === 'queued' && item.status === 'queued'));
      const compare: Record<string, (a: TaskFeedItem, b: TaskFeedItem) => number> = {
        createdAt_desc: (a, b) => (a.createdAt < b.createdAt ? 1 : a.createdAt > b.createdAt ? -1 : a.key.localeCompare(b.key)),
        createdAt_asc: (a, b) => (a.createdAt > b.createdAt ? 1 : a.createdAt < b.createdAt ? -1 : a.key.localeCompare(b.key)),
        title_asc: (a, b) => a.title.localeCompare(b.title, 'zh-CN') || a.key.localeCompare(b.key),
      };
      filtered.sort(compare[sort] ?? compare.createdAt_desc);

      const page = Math.max(1, Number(ctx.params.page) || 1);
      const pageSize = Math.max(1, Math.min(100, Number(ctx.params.pageSize) || 24));
      return {
        items: filtered.slice((page - 1) * pageSize, page * pageSize), total: filtered.length,
        page, pageSize, stats, canReadAll: true,
        warnings: source === 'replacement' || source === 'all'
          ? ['演示模式不读取真实替换档案，请连接后端查看替换任务。'] : [],
      };
    },
  },
  {
    method: 'GET',
    pattern: /^\/tasks$/,
    handler: listTasks,
  },
  {
    // 必须排在详情路由之前：否则 /tasks/stats 会被 /^\/tasks\/([\w-]+)$/ 吃掉
    method: 'GET',
    pattern: /^\/tasks\/stats$/,
    handler: (ctx) => listTasks({ ...ctx, params: { ...ctx.params, pageSize: 1 } }).stats,
  },
  {
    method: 'GET',
    pattern: /^\/tasks\/([\w-]+)$/,
    handler: (ctx) => {
      const id = ctx.path.split('/').pop();
      const task = db.tasks.find((t) => t.id === id);
      if (!task) throw new HttpError(404, '任务不存在');
      return task;
    },
  },
  {
    method: 'POST',
    pattern: /^\/tasks$/,
    handler: (ctx) => {
      const prompt = String(ctx.body.prompt ?? '').trim();
      if (!prompt) throw new HttpError(400, '画面描述不能为空');
      const task = db.addTask({
        title: String(ctx.body.title ?? prompt.slice(0, 24)),
        prompt,
        model: String(ctx.body.model ?? 'Seedance 2.5'),
        durationSec: Number(ctx.body.durationSec ?? 15),
        aspectRatio: (ctx.body.aspectRatio as AspectRatio) ?? '9:16',
      });
      return task;
    },
    status: 201,
  },
  {
    method: 'POST',
    pattern: /^\/tasks\/([\w-]+)\/retry$/,
    handler: (ctx) => {
      const id = ctx.path.split('/')[2];
      const task = db.tasks.find((t) => t.id === id);
      if (!task) throw new HttpError(404, '任务不存在');
      task.status = 'queued';
      task.progress = 0;
      task.errorMessage = undefined;
      task.updatedAt = new Date().toISOString();
      return task;
    },
  },
  {
    method: 'PATCH',
    pattern: /^\/tasks\/([\w-]+)$/,
    handler: (ctx) => {
      const id = ctx.path.split('/').pop();
      const task = db.tasks.find((t) => t.id === id);
      if (!task) throw new HttpError(404, '任务不存在');
      // 批量收藏走这里：`favorited: true` 是显式置位，不是切换
      if (typeof ctx.body.title === 'string') {
        task.title = ctx.body.title.trim().slice(0, 200) || '未命名任务';
      }
      if (typeof ctx.body.favorited === 'boolean') task.favorited = ctx.body.favorited;
      task.updatedAt = new Date().toISOString();
      return task;
    },
  },
  {
    method: 'POST',
    pattern: /^\/tasks\/([\w-]+)\/favorite$/,
    handler: (ctx) => {
      const id = ctx.path.split('/')[2];
      const task = db.tasks.find((t) => t.id === id);
      if (!task) throw new HttpError(404, '任务不存在');
      task.favorited = !task.favorited;
      return task;
    },
  },
  {
    method: 'DELETE',
    pattern: /^\/tasks$/,
    handler: (ctx) => {
      const ids = Array.isArray(ctx.body.ids) ? (ctx.body.ids as string[]) : [];
      const found = new Set(db.tasks.filter((t) => ids.includes(t.id)).map((t) => t.id));
      db.tasks = db.tasks.filter((t) => !ids.includes(t.id));
      return { deleted: found.size, skipped: ids.filter((id) => !found.has(id)) };
    },
  },
];
