import { act, render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TaskCompletionWatcher } from './TaskCompletionWatcher';

/**
 * 用一个可变的 `feed` 模拟轮询：每次重渲染读到最新的那一份数据。
 * 桌面通知的坑全在「两次快照之间」，所以这里必须能控制「上一轮 / 这一轮」。
 */
/* vi.mock 会被提升到文件顶部，工厂里只能用 vi.hoisted 造出来的变量 */
const state = vi.hoisted(() => ({
  feed: undefined as { items: { key: string; group: string; title: string }[] } | undefined,
  toast: vi.fn(),
}));

const toastMock = state.toast;

vi.mock('@/entities/task/api/task.queries', () => ({
  useTaskFeed: () => ({ data: state.feed }),
}));
vi.mock('@/shared/lib/toast', () => ({ toast: state.toast }));

function renderWith(feedValue: typeof state.feed) {
  state.feed = feedValue;
  return render(
    <MemoryRouter>
      <TaskCompletionWatcher />
    </MemoryRouter>,
  );
}

const item = (group: string, key = 'video:t1', title = '芳华里') => ({ key, group, title });

describe('TaskCompletionWatcher', () => {
  beforeEach(() => {
    state.feed = undefined;
    toastMock.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('首次加载不弹：历史任务不该在刚打开工作台时糊一屏', () => {
    renderWith({ items: [item('completed')] });
    expect(toastMock).not.toHaveBeenCalled();
  });

  it('在跑 → 完成，弹一条成功的', () => {
    const { rerender } = renderWith({ items: [item('running')] });

    state.feed = { items: [item('completed')] };
    rerender(
      <MemoryRouter>
        <TaskCompletionWatcher />
      </MemoryRouter>,
    );

    expect(toastMock).toHaveBeenCalledTimes(1);
    expect(toastMock.mock.calls[0][0]).toMatchObject({
      message: '「芳华里」已完成',
      tone: 'success',
      key: 'task:video:t1',
    });
  });

  it('失败用 error 色调，文案说「生成失败」', () => {
    const { rerender } = renderWith({ items: [item('queued')] });

    state.feed = { items: [item('failed')] };
    rerender(
      <MemoryRouter>
        <TaskCompletionWatcher />
      </MemoryRouter>,
    );

    expect(toastMock.mock.calls[0][0]).toMatchObject({ message: '「芳华里」生成失败', tone: 'error' });
  });

  it('同一个任务只弹一次：轮询会反复读到同一条', () => {
    const { rerender } = renderWith({ items: [item('running')] });

    state.feed = { items: [item('completed')] };
    rerender(
      <MemoryRouter>
        <TaskCompletionWatcher />
      </MemoryRouter>,
    );
    rerender(
      <MemoryRouter>
        <TaskCompletionWatcher />
      </MemoryRouter>,
    );

    expect(toastMock).toHaveBeenCalledTimes(1);
  });

  it('标签页隐藏时不弹，也不推进基线 —— 回来能补报', () => {
    const spy = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden');
    const { rerender } = renderWith({ items: [item('running')] });

    state.feed = { items: [item('completed')] };
    rerender(
      <MemoryRouter>
        <TaskCompletionWatcher />
      </MemoryRouter>,
    );
    expect(toastMock).not.toHaveBeenCalled();

    spy.mockReturnValue('visible');
    act(() => {
      document.dispatchEvent(new Event('visibilitychange'));
    });

    expect(toastMock).toHaveBeenCalledTimes(1);
  });

  it('一次错过多条，合并成一条汇总', () => {
    const { rerender } = renderWith({
      items: [item('running', 'video:a'), item('running', 'video:b'), item('running', 'video:c')],
    });

    state.feed = {
      items: [item('completed', 'video:a'), item('failed', 'video:b'), item('completed', 'video:c')],
    };
    rerender(
      <MemoryRouter>
        <TaskCompletionWatcher />
      </MemoryRouter>,
    );

    expect(toastMock).toHaveBeenCalledTimes(1);
    expect(toastMock.mock.calls[0][0]).toMatchObject({
      message: '3 个任务已结束，其中 1 个失败',
      tone: 'warning',
    });
  });
});
