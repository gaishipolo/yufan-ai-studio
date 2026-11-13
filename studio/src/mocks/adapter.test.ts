import { AxiosHeaders, type InternalAxiosRequestConfig } from 'axios';
import { describe, expect, it } from 'vitest';
import { mockAdapter } from './adapter';
import { db } from './db';
import type { ApiResponse } from '@/shared/api/types';
import type { TaskFeedResult } from '@/entities/task/model/feed.types';
import type { TaskListResult, VideoTask } from '@/entities/task/model/types';

interface MockResult<T> {
  status: number;
  body: ApiResponse<T>;
}

/**
 * 直接把 axios 配置喂给 adapter，绕过网络层。
 * 这里必须断言：adapter 的入参是一个完整 InternalAxiosRequestConfig（含 headers），
 * 测试里只关心 url/method/params/data，因此用一次类型断言收窄，避免造一个假的请求对象。
 */
async function call<T>(
  method: string,
  url: string,
  options: { params?: Record<string, unknown>; data?: unknown } = {},
): Promise<MockResult<T>> {
  const config = {
    url,
    method,
    params: options.params,
    data: options.data === undefined ? undefined : JSON.stringify(options.data),
    headers: new AxiosHeaders(),
  } as unknown as InternalAxiosRequestConfig;

  const response = await mockAdapter(config);
  return { status: response.status, body: response.data as ApiResponse<T> };
}

const listTasks = (params: Record<string, unknown>) =>
  call<TaskListResult>('get', '/tasks', { params });

describe('mock adapter · 任务列表', () => {
  it('scope=mine 只返回本人任务（越权回归）', async () => {
    const { body } = await listTasks({ scope: 'mine', page: 1, pageSize: 50 });
    expect(body.data?.items.length).toBeGreaterThan(0);
    expect(body.data?.items.every((task) => task.authorId === 'u_001')).toBe(true);
  });

  it('scope=all 能看到他人任务，且总数不少于 mine', async () => {
    const mine = await listTasks({ scope: 'mine', pageSize: 50 });
    const all = await listTasks({ scope: 'all', pageSize: 50 });

    expect(all.body.data?.total).toBeGreaterThanOrEqual(mine.body.data?.total ?? 0);
    expect(all.body.data?.items.some((task) => task.authorId !== 'u_001')).toBe(true);
  });

  it('status 筛选只影响本次返回，不影响 stats 口径', async () => {
    const { body } = await listTasks({ status: 'failed', scope: 'all', pageSize: 50 });
    const items = body.data?.items ?? [];

    expect(items.every((task) => task.status === 'failed')).toBe(true);
    // stats 反映的是「筛选状态之前」的分布，因此 all 一定大于 failed
    expect(body.data?.stats.all).toBeGreaterThan(items.length);
    expect(body.data?.stats.failed).toBe(items.length);
  });

  it('keyword 命中标题 / 模型 / ID 三类字段', async () => {
    const { body } = await listTasks({ keyword: '冰川', scope: 'all', pageSize: 50 });
    const items = body.data?.items ?? [];

    expect(items.length).toBeGreaterThan(0);
    expect(
      items.every((task) => [task.title, task.model, task.id].some((field) => field.includes('冰川'))),
    ).toBe(true);
  });

  it('分页只切当前页，total 保持全集数量', async () => {
    const all = await listTasks({ scope: 'all', page: 1, pageSize: 100 });
    const first = await listTasks({ scope: 'all', page: 1, pageSize: 5 });
    const second = await listTasks({ scope: 'all', page: 2, pageSize: 5 });

    expect(first.body.data?.items).toHaveLength(5);
    expect(first.body.data?.total).toBe(all.body.data?.total);
    expect(first.body.data?.items[0].id).not.toBe(second.body.data?.items[0].id);
  });
});

describe('mock adapter · 单个任务', () => {
  it('详情按 id 命中', async () => {
    const list = await listTasks({ scope: 'all', pageSize: 1 });
    const id = list.body.data?.items[0].id as string;

    const { body } = await call<VideoTask>('get', `/tasks/${id}`);
    expect(body.data?.id).toBe(id);
  });

  it('不存在的任务返回 404 语义的业务错误', async () => {
    await expect(call('get', '/tasks/does-not-exist')).rejects.toThrow('任务不存在');
  });

  it('未实现的接口直接报错，不会静默返回空数据', async () => {
    await expect(call('get', '/unknown-endpoint')).rejects.toThrow(/未实现的接口/);
  });
});

describe('mock adapter · 写操作', () => {
  it('创建任务返回 201，且新任务出现在列表首位', async () => {
    const payload = {
      prompt: '测试用画面描述',
      model: 'seedance-2.5',
      durationSec: 5,
      aspectRatio: '9:16',
    };
    const created = await call<VideoTask>('post', '/tasks', { data: payload });

    expect(created.status).toBe(201);
    expect(created.body.data?.status).toBe('queued');

    const list = await listTasks({ scope: 'mine', page: 1, pageSize: 100 });
    expect(list.body.data?.items[0].id).toBe(created.body.data?.id);
  });

  it('缺少画面描述时拒绝创建', async () => {
    await expect(call('post', '/tasks', { data: { prompt: '   ' } })).rejects.toThrow(
      '画面描述不能为空',
    );
  });

  it('收藏是幂等切换：连续两次回到原值', async () => {
    const list = await listTasks({ scope: 'all', pageSize: 1 });
    const task = list.body.data?.items[0] as VideoTask;
    const before = task.favorited;

    const once = await call<VideoTask>('post', `/tasks/${task.id}/favorite`);
    // mock 的响应直接引用内存里的任务对象，必须在第二次请求前把值取出来
    const afterToggle = once.body.data?.favorited;
    const twice = await call<VideoTask>('post', `/tasks/${task.id}/favorite`);
    const afterSecondToggle = twice.body.data?.favorited;

    expect(afterToggle).toBe(!before);
    expect(afterSecondToggle).toBe(before);
  });
});

describe('mock adapter · 任务中心三路聚合与批量操作', () => {
  it('source=image 只返回生图，并带上生图投影', async () => {
    const { body } = await call<TaskFeedResult>('get', '/tasks/feed', {
      params: { source: 'image', pageSize: 100 },
    });
    const items = body.data?.items ?? [];
    expect(items.length).toBeGreaterThan(0);
    expect(items.every((item) => item.source === 'image' && item.image)).toBe(true);
    expect(body.data?.stats.all).toBe(items.length);
  });

  it('PATCH 显式置收藏是幂等置位，不是切换', async () => {
    const list = await listTasks({ scope: 'all', pageSize: 1 });
    const id = list.body.data?.items[0].id as string;
    const once = await call<VideoTask>('patch', `/tasks/${id}`, { data: { favorited: true } });
    const twice = await call<VideoTask>('patch', `/tasks/${id}`, { data: { favorited: true } });
    expect(once.body.data?.favorited).toBe(true);
    expect(twice.body.data?.favorited).toBe(true);
  });

  it('生图重试把失败记录重新排队，非失败记录被拒绝', async () => {
    const job = db.imageJobs[0];
    const snapshot = { ...job };
    job.status = 'failed';
    try {
      const { body } = await call<{ id: string; status: string }>(
        'post', `/images/${job.id}/retry`,
      );
      expect(body.data?.status).toBe('queued');
      await expect(call('post', `/images/${db.imageJobs[1].id}/retry`)).rejects.toThrow(
        '仅失败的生图任务可以重试',
      );
    } finally {
      Object.assign(job, snapshot);
    }
  });

  it('批量删除如实回报 skipped，不把不存在的 id 算成已删', async () => {
    const list = await listTasks({ scope: 'all', pageSize: 1 });
    const id = list.body.data?.items[0].id as string;
    const { body } = await call<{ deleted: number; skipped: string[] }>('delete', '/tasks', {
      data: { ids: [id, 'not-exist'] },
    });
    expect(body.data?.deleted).toBe(1);
    expect(body.data?.skipped).toEqual(['not-exist']);
  });
});
