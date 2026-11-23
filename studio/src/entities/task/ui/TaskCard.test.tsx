import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { TaskCard } from './TaskCard';
import type { VideoTask } from '../model/types';

const task: VideoTask = {
  id: 'T20261105001',
  title: '超市抢购场景 补拍',
  status: 'completed',
  model: 'Seedance 2.5',
  modelId: 'seedance-2.5',
  durationSec: 5,
  aspectRatio: '9:16',
  progress: 100,
  coverUrl: 'https://example.com/cover.jpg',
  prompt: '超市抢购场景',
  authorId: 'u_001',
  authorName: '老大',
  favorited: false,
  downloadable: true,
  createdAt: '2026-11-05T03:32:13.301Z',
  updatedAt: '2026-11-05T03:32:21.375Z',
};

function setup(
  overrides: Partial<VideoTask> = {},
  selected = false,
  queueHint?: string | null,
) {
  const onToggleSelect = vi.fn();
  const onOpen = vi.fn();
  const view = render(
    <TaskCard
      task={{ ...task, ...overrides }}
      selected={selected}
      onToggleSelect={onToggleSelect}
      onOpen={onOpen}
      queueHint={queueHint}
    />,
  );
  return { ...view, onToggleSelect, onOpen };
}

describe('TaskCard', () => {
  it('整卡是一个可访问的按钮，点击打开详情', async () => {
    const user = userEvent.setup();
    const { onOpen } = setup();

    await user.click(screen.getByRole('button', { name: '查看任务：超市抢购场景 补拍' }));
    expect(onOpen).toHaveBeenCalledWith('T20261105001');
  });

  it('键盘 Enter 也能打开（不再依赖鼠标）', async () => {
    const user = userEvent.setup();
    const { onOpen } = setup();

    const card = screen.getByRole('button', { name: '查看任务：超市抢购场景 补拍' });
    card.focus();
    await user.keyboard('{Enter}');

    expect(onOpen).toHaveBeenCalled();
  });

  it('勾选按钮是独立动作，不会顺带打开详情', async () => {
    const user = userEvent.setup();
    const { onOpen, onToggleSelect } = setup();

    await user.click(screen.getByRole('button', { name: '选择任务：超市抢购场景 补拍' }));

    expect(onToggleSelect).toHaveBeenCalledWith('T20261105001');
    expect(onOpen).not.toHaveBeenCalled();
  });

  it('已选中时 aria-pressed 与文案同步，读屏能分辨状态', () => {
    setup({}, true);

    const button = screen.getByRole('button', { name: '取消选择：超市抢购场景 补拍' });
    expect(button).toHaveAttribute('aria-pressed', 'true');
  });

  it('生成中的卡片展示进度而不是创建时间', () => {
    setup({ status: 'running', progress: 42 });

    expect(screen.getByText('生成中')).toBeInTheDocument();
    expect(screen.getByText('42%')).toBeInTheDocument();
    expect(screen.queryByText('11-05 03:32')).not.toBeInTheDocument();
  });
});

describe('TaskCard · 上游排队', () => {
  it('排队时在进度条下面多一行说明', () => {
    // 「48% 卡了半小时」和「排队第 3 位」看起来一模一样，差别只有这一行
    setup({ status: 'running', progress: 12 }, false, '0 积分队列 3/10');

    expect(screen.getByText('0 积分队列 3/10')).toBeInTheDocument();
  });

  it('没有排队信息时不占位（真在生成时百分比已经够说明了）', () => {
    setup({ status: 'running', progress: 48 });

    expect(screen.queryByText(/积分队列/)).not.toBeInTheDocument();
    expect(screen.getByText('48%')).toBeInTheDocument();
  });
});

it('已完成父卡仍显示去字幕进度和失败原因', () => {
  const task = {
    id: 'parent', title: '成片', status: 'completed', model: '测试',
    coverUrl: '', aspectRatio: '9:16', durationSec: 4, createdAt: '2026-11-20T00:00:00Z',
    subtitleErase: { id: 'child', status: 'running', progress: 42 },
  } as VideoTask;
  const props = { selected: false, onToggleSelect: vi.fn(), onOpen: vi.fn() };
  const view = render(<TaskCard task={task} {...props} />);
  expect(screen.getByText('去字幕 42%')).toBeInTheDocument();
  view.rerender(<TaskCard task={{ ...task, subtitleErase: {
    id: 'child', status: 'failed', progress: 42, errorMessage: '审核失败',
  } }} {...props} />);
  expect(screen.getByText(/去字幕失败/)).toHaveAttribute('title', '审核失败');
});
