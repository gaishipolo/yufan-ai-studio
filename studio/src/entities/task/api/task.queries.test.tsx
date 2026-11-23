import { act, renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider, type QueryObserverOptions } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { taskApi } from './task.api';
import { useTaskDetail, useTaskFeed, useTaskList } from './task.queries';
import type { VideoTask } from '../model/types';
import type { TaskFeedResult } from '../model/feed.types';

vi.mock('./task.api', () => ({ taskApi: { detail: vi.fn(), feed: vi.fn(), list: vi.fn() } }));
vi.mock('@/shared/access/session', () => ({
  useSessionStore: () => 'owner',
}));

const parent = {
  id: 'parent', status: 'completed',
  subtitleErase: { id: 'child', status: 'running', progress: 42 },
} as VideoTask;

describe('去字幕进度轮询', () => {
  it.each(['detail', 'list', 'feed'] as const)('%s 在父任务完成后继续跟踪派生任务', async (kind) => {
    let finish = false;
    const current = () => ({
      ...parent,
      subtitleErase: { ...parent.subtitleErase!, status: finish ? 'completed' : 'running' },
    });
    vi.mocked(taskApi.detail).mockImplementation(async () => current() as VideoTask);
    vi.mocked(taskApi.list).mockImplementation(async () => ({ items: [current()] }) as never);
    vi.mocked(taskApi.feed).mockImplementation(async () => ({
      items: [{ video: current() }], stats: { running: 0 },
    }) as TaskFeedResult);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const { result, unmount } = renderHook(() => {
      const detail = useTaskDetail(kind === 'detail' ? 'parent' : null);
      const list = useTaskList({});
      const feed = useTaskFeed({});
      return { detail, list, feed }[kind];
    }, { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    const query = client.getQueryCache().getAll().find(q => q.state.data && (
      kind === 'detail' ? q.queryKey.includes('detail') : q.queryKey.includes(kind)
    ))!;
    const interval = (query.options as QueryObserverOptions).refetchInterval;
    expect(typeof interval).toBe('function');
    expect((interval as (q: typeof query) => number | false)(query)).toBeGreaterThan(0);
    finish = true;
    await act(async () => { await client.refetchQueries(); });
    expect((interval as (q: typeof query) => number | false)(query)).toBe(false);
    unmount();
    client.clear();
  });
});
