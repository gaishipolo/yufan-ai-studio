import type { AspectRatio, TaskStatus, VideoTask } from '@/entities/task/model/types';
import { db } from '../db';
import { HttpError, type MockContext, type MockRoute } from '../kernel';

/* ---------------- 任务 ---------------- */

function listTasks(ctx: MockContext) {
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
  const ps = Math.max(1, Math.min(100, Number(pageSize) || 24));
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

export const routes: MockRoute[] = [
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
      db.tasks = db.tasks.filter((t) => !ids.includes(t.id));
      return { deleted: ids.length };
    },
  },
];
