import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AccessProvider } from '@/shared/access/AccessProvider';
import type { VideoTask } from '@/entities/task/model/types';
import type { TaskFeedItem, TaskFeedParams, TaskFeedResult } from '@/entities/task/model/feed.types';
import TaskCenterPage from './TaskCenterPage';

const mock = vi.hoisted(() => ({
  list: {} as Record<string, unknown>,
  params: {} as TaskFeedParams,
  deleteCalls: [] as string[][],
  deleteImageCalls: [] as string[][],
  updateCalls: [] as unknown[],
  deleted: { deleted: 1, skipped: [] as string[] },
}));

vi.mock('@/entities/task/api/task.queries', () => ({
  useTaskFeed: (params: TaskFeedParams) => {mock.params = params; return mock.list;},
  useTaskDetail: () => ({data: undefined}),
  useCreateTask: () => ({mutateAsync: vi.fn(), isPending: false}),
  useRetryTask: () => ({mutateAsync: vi.fn(), isPending: false}),
  useToggleFavorite: () => ({mutateAsync: vi.fn(), isPending: false}),
  // 任务卡与弹窗都会用到；返回同形状的空 mutation，避免「mock 缺导出」整页崩
  useDesubtitle: () => ({mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false}),
  useDeleteTasks: () => ({
    mutateAsync: vi.fn(async (ids: string[]) => { mock.deleteCalls.push(ids); return mock.deleted; }),
    isPending: false,
  }),
  useDeleteImages: () => ({
    mutateAsync: vi.fn(async (ids: string[]) => { mock.deleteImageCalls.push(ids); return mock.deleted; }),
    isPending: false,
  }),
  useUpdateTask: () => ({
    mutateAsync: vi.fn(async (patch: unknown) => { mock.updateCalls.push(patch); return {}; }),
    isPending: false,
  }),
  taskKeys: { all: ['tasks'], feeds: () => ['tasks', 'feed'] },
}));
vi.mock('@/shared/lib/toast', () => ({toast: vi.fn()}));

const task: VideoTask = {
  id: 'task1', title: '测试任务', status: 'completed', model: 'Seedance 2.5',
  durationSec: 5, aspectRatio: '9:16', progress: 100, coverUrl: '/cover/task1',
  prompt: '提示词', authorId: 'u_test', authorName: '测试', favorited: false,
  downloadable: true, createdAt: '2026-11-06T02:00:00Z', updatedAt: '2026-11-06T02:05:00Z',
};

function listResult(overrides: Partial<TaskFeedResult> = {}): TaskFeedResult {
  return {
    items: [{key: 'video:task1', id: task.id, title: task.title, status: task.status, group: 'completed', source: 'video', createdAt: task.createdAt, progress: 100, sourceName: '', video: task}], total: 1, page: 1, pageSize: 48,
    stats: {all: 1, running: 0, completed: 1, failed: 0, attention: 0},
    canReadAll: false, warnings: [],
    ...overrides,
  };
}

function App({permissions = ['task:read']}: {permissions?: string[]}) {
  /* 任务中心不再挂 CreateTaskModal（任务只能从生成工作台 / 模板库发起），
     但页面仍要读任务列表，留着 QueryClient 以防后续接入查询 */
  const client = new QueryClient({defaultOptions: {queries: {retry: false}}});
  return <QueryClientProvider client={client}>
    <MemoryRouter initialEntries={['/tasks']}>
      <AccessProvider subject={{id: 'u_test', name: '测试', roles: [], permissions}}>
        <TaskCenterPage />
      </AccessProvider>
    </MemoryRouter>
  </QueryClientProvider>;
}

beforeEach(() => {
  mock.list = {data: listResult(), isLoading: false, isFetching: false, isError: false, error: null, refetch: vi.fn()};
  mock.deleteCalls = [];
  mock.deleteImageCalls = [];
  mock.updateCalls = [];
  mock.deleted = { deleted: 1, skipped: [] };
});

describe('任务中心页面', () => {
  it('来源与待处理筛选传给聚合接口', () => {
    render(<App permissions={['task:read', 'image:read']} />);
    fireEvent.change(screen.getByRole('combobox', {name: '任务来源'}), {target: {value: 'image'}});
    expect(mock.params.source).toBe('image');
    fireEvent.click(screen.getByRole('button', {name: /待处理/}));
    expect(mock.params.status).toBe('attention');
    expect(mock.params.page).toBe(1);
  });

  it('没有 image:read 时不提供生图来源与入口', () => {
    render(<App permissions={['task:read']} />);
    expect(screen.queryByRole('option', {name: '生图'})).not.toBeInTheDocument();
    expect(screen.queryByRole('link', {name: /生图工作台/})).not.toBeInTheDocument();
    // 模板库不需要 task:read，所以「从模板生成」始终在
    expect(screen.getByRole('link', {name: /从模板生成/})).toHaveAttribute('href', '/templates/library');
  });

  it('某个来源读不出来时显示告警，但不挡住其他来源', () => {
    mock.list.data = listResult({warnings: ['生图任务暂时无法读取，稍后刷新。']});
    render(<App />);
    expect(screen.getByRole('alert')).toHaveTextContent('生图任务暂时无法读取');
    expect(screen.getByRole('button', {name: '查看任务：测试任务'})).toBeVisible();
  });

  const imageItem: TaskFeedItem = {
    key: 'image:img1', id: 'img1', source: 'image', title: '一只猫', status: 'succeeded',
    group: 'completed', createdAt: task.createdAt, progress: 100, sourceName: '',
    outputUrl: '/static/cat.png',
    image: {
      id: 'img1', title: '一只猫', status: 'succeeded', group: 'completed',
      createdAt: task.createdAt, progress: 100, model: '生图模型', size: '1024x1024',
      count: 1, prompt: '一只猫', coverUrl: '/static/cat.png', outputUrl: '/static/cat.png',
      detailUrl: '/image?job=img1',
    },
  };

  it('生图作为第三类来源展示，并可按来源筛选', () => {
    mock.list.data = listResult({items: [...listResult().items, imageItem], total: 2});
    render(<App permissions={['task:read', 'image:read']} />);
    expect(screen.getByRole('button', {name: '查看生图：一只猫'})).toBeVisible();
    expect(screen.getByRole('option', {name: '生图'})).toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox', {name: '任务来源'}), {target: {value: 'image'}});
    expect(mock.params.source).toBe('image');
  });

  it('批量删除走真实接口，且必须先过二次确认', async () => {
    mock.deleted = {deleted: 1, skipped: ['task-missing']};
    render(<App permissions={['task:read', 'task:delete']} />);
    expect(screen.queryByRole('button', {name: '回收站'})).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name: '选择任务：测试任务'}));
    fireEvent.click(screen.getByRole('button', {name: '删除'}));

    // 硬删不可恢复：第一下只弹确认，不能直接把请求发出去
    expect(await screen.findByRole('dialog')).toHaveTextContent('删除选中的任务');
    expect(mock.deleteCalls).toEqual([]);

    fireEvent.click(screen.getByRole('button', {name: '确认删除'}));
    await waitFor(() => expect(mock.deleteCalls).toEqual([['task1']]));
  });

  it('取消确认则一条都不删', async () => {
    render(<App permissions={['task:read', 'task:delete']} />);
    fireEvent.click(screen.getByRole('button', {name: '选择任务：测试任务'}));
    fireEvent.click(screen.getByRole('button', {name: '删除'}));
    fireEvent.click(await screen.findByRole('button', {name: '取消'}));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(mock.deleteCalls).toEqual([]);
  });

  it('批量收藏用显式置位而不是切换', async () => {
    render(<App permissions={['task:read', 'task:update']} />);
    fireEvent.click(screen.getByRole('button', {name: '选择任务：测试任务'}));
    fireEvent.click(screen.getByRole('button', {name: '收藏'}));
    await waitFor(() => expect(mock.updateCalls).toEqual([{id: 'task1', favorited: true}]));
  });

  it('勾选生图记录后删除走 DELETE /images，不和视频混批', async () => {
    mock.list.data = listResult({items: [...listResult().items, imageItem], total: 2});
    render(<App permissions={['task:read', 'image:read', 'image:delete']} />);
    fireEvent.click(screen.getByRole('button', {name: '选择生图：一只猫'}));
    fireEvent.click(screen.getByRole('button', {name: '删除'}));
    fireEvent.click(await screen.findByRole('button', {name: '确认删除'}));
    await waitFor(() => expect(mock.deleteImageCalls).toEqual([['img1']]));
    // 视频那条没被选中，不该跟着发出去：两套表两个端点，混批会有一半被 skipped
    expect(mock.deleteCalls).toEqual([]);
  });
});

describe('写操作按权限渲染', () => {
  it('只读账号看不到收藏与删除', () => {
    render(<App permissions={['task:read', 'image:read']} />);

    fireEvent.click(screen.getByRole('button', {name: '选择任务：测试任务'}));
    expect(screen.queryByRole('button', {name: '删除'})).not.toBeInTheDocument();
    expect(screen.queryByRole('button', {name: '收藏'})).not.toBeInTheDocument();
    // 下载只读也要能点：它不写库
    expect(screen.getByRole('button', {name: '下载'})).toBeVisible();
  });

  /* 任务中心**不提供**创建入口：任务只能从生成工作台 / 模板库发起。
     这里的列表页是「看结果」的，不是「发起」的 —— 在这里放个新建按钮，
     等于给出第二条绕过工作台参数的创建路径（工作台的参数面是模型声明驱动的，
     这里的表单是另一套）。 */
  it('任务中心没有创建任务入口，哪怕账号有 task:create', () => {
    render(<App permissions={['task:read', 'task:create']} />);

    expect(screen.queryByRole('button', {name: '创建任务'})).not.toBeInTheDocument();
    expect(screen.queryByText('创建视频任务')).not.toBeInTheDocument();
  });
});
