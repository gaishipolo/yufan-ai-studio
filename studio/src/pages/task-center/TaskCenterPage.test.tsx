import { fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AccessProvider } from '@/shared/access/AccessProvider';
import type { VideoTask } from '@/entities/task/model/types';
import type { TaskFeedParams, TaskFeedResult } from '@/entities/task/model/feed.types';
import TaskCenterPage from './TaskCenterPage';

const mock = vi.hoisted(() => ({ list: {} as Record<string, unknown>, params: {} as TaskFeedParams }));

vi.mock('@/entities/task/api/task.queries', () => ({
  useTaskFeed: (params: TaskFeedParams) => {mock.params = params; return mock.list;},
  useTaskDetail: () => ({data: undefined}),
  useCreateTask: () => ({mutateAsync: vi.fn(), isPending: false}),
  useRetryTask: () => ({mutateAsync: vi.fn(), isPending: false}),
  useToggleFavorite: () => ({mutateAsync: vi.fn(), isPending: false}),
  useDeleteTasks: () => ({mutateAsync: vi.fn(), isPending: false}),
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
  /* 页面挂载即渲染 CreateTaskModal，它会读模型列表，必须给 QueryClient */
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
});

describe('任务中心页面', () => {
  it('统一展示通用任务与替换任务，档案链接包含原始 run id', () => {
    mock.list.data = listResult({items: [...listResult().items, {
      key: 'replacement:run1', id: 'run1', source: 'replacement', title: '原片 · 人物替换 · 小雨',
      status: 'waiting_free', group: 'attention', progress: 0, sourceName: '原片',
      createdAt: task.createdAt, detailUrl: '/templates?kind=character&run=run1',
    }], total: 2});
    render(<App permissions={['task:read', 'template:read']} />);
    expect(screen.getByRole('button', {name: '查看任务：测试任务'})).toBeVisible();
    expect(screen.getByText('报价非零 · 等待免费')).toBeVisible();
    expect(screen.getByRole('link', {name: '查看替换档案 →'})).toHaveAttribute('href', '/templates?kind=character&run=run1');
    expect(screen.getByRole('link', {name: /创建替换任务/})).toHaveAttribute('href', '/templates');
  });

  it('来源与待处理筛选传给聚合接口', () => {
    render(<App permissions={['task:read', 'template:read']} />);
    fireEvent.change(screen.getByRole('combobox', {name: '任务来源'}), {target: {value: 'replacement'}});
    expect(mock.params.source).toBe('replacement');
    fireEvent.click(screen.getByRole('button', {name: /待处理/}));
    expect(mock.params.status).toBe('attention');
    expect(mock.params.page).toBe(1);
  });

  it('没有 template:read 时不提供替换任务来源选项或创建入口', () => {
    render(<App permissions={['task:read']} />);
    expect(screen.queryByRole('option', {name: '模板替换'})).not.toBeInTheDocument();
    expect(screen.queryByRole('link', {name: /创建替换任务/})).not.toBeInTheDocument();
  });

  it('替换归档盘离线时明确显示统计不完整，仍展示通用任务', () => {
    mock.list.data = listResult({warnings: ['替换任务暂时无法读取，当前统计不含替换任务。']});
    render(<App />);
    expect(screen.getByRole('alert')).toHaveTextContent('当前统计不含替换任务');
    expect(screen.getByRole('button', {name: '查看任务：测试任务'})).toBeVisible();
  });
});
