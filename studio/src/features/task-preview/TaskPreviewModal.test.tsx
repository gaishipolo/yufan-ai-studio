import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import { ApiError } from '@/shared/api/types';
import { TaskPreviewModal } from './TaskPreviewModal';
import type { VideoTask } from '@/entities/task/model/types';

const mocks = vi.hoisted(() => ({
  task: {} as VideoTask, retry: vi.fn(), create: vi.fn(), download: vi.fn(),
}));
vi.mock('@/entities/task/api/task.queries', () => ({
  useTaskDetail: () => ({ data: mocks.task, isLoading: false }),
  useRetryTask: () => ({ mutate: mocks.retry, isPending: false }),
  useDesubtitle: () => ({ mutate: mocks.create, isPending: false }),
  useToggleFavorite: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock('@/shared/access/use-access', () => ({ useCan: () => true }));
vi.mock('@/entities/libtv/api/libtv.queries', () => ({ useLibtvQueue: () => ({}) }));
vi.mock('@/shared/lib/download', () => ({ downloadUrl: (...args: unknown[]) => mocks.download(...args) }));
vi.mock('./PromptField', () => ({ PromptField: () => null }));
vi.mock('@/shared/ui', async (load) => {
  const actual = await load<Record<string, unknown>>();
  return { ...actual, Modal: ({ children }: { children: ReactNode }) => <div>{children}</div> };
});

beforeEach(() => {
  localStorage.clear();
  mocks.task = {
    id: 'parent-a', title: '测试成片', status: 'completed', progress: 100,
    model: '测试', modelId: 'test', durationSec: 4, aspectRatio: '9:16',
    prompt: '', authorId: 'owner', authorName: '测试', favorited: false,
    downloadable: true, coverUrl: '', createdAt: '2026-11-20T00:00:00Z',
    updatedAt: '2026-11-20T00:00:00Z',
    subtitleErase: { id: 'child-a', status: 'completed', progress: 100, outputUrl: '/erased.mp4' },
  };
});

describe('去字幕播放器', () => {
  it('去字幕是下载、重新生成、收藏后的第四个操作', () => {
    render(<TaskPreviewModal taskId="parent-a" outputUrl="/original.mp4" onClose={vi.fn()} />);
    const names = screen.getAllByRole('button').map(button => button.textContent)
      .filter(name => ['下载成片', '重新生成', '收藏', '已去字幕'].includes(name ?? ''));
    expect(names).toEqual(['下载成片', '重新生成', '收藏', '已去字幕']);
  });
  it('默认播放去字幕版，切换原片后下载跟随，重新打开保留选择', () => {
    const props = { taskId: 'parent-a', outputUrl: '/original.mp4', onClose: vi.fn() };
    const view = render(<TaskPreviewModal {...props} />);
    expect(screen.getByLabelText('成片：测试成片')).toHaveAttribute('src', '/erased.mp4');
    fireEvent.click(screen.getByRole('button', { name: '下载成片' }));
    expect(mocks.download).toHaveBeenLastCalledWith('/erased.mp4', expect.stringContaining('-去字幕.mp4'));
    fireEvent.click(screen.getByRole('button', { name: '原片' }));
    expect(screen.getByLabelText('成片：测试成片')).toHaveAttribute('src', '/original.mp4');
    fireEvent.click(screen.getByRole('button', { name: '下载成片' }));
    expect(mocks.download).toHaveBeenLastCalledWith('/original.mp4', expect.not.stringContaining('-去字幕'));
    view.unmount();
    render(<TaskPreviewModal {...props} />);
    expect(screen.getByLabelText('成片：测试成片')).toHaveAttribute('src', '/original.mp4');
  });

  it('切到另一张卡时默认去字幕版，不继承上一张卡的原片选择', () => {
    const view = render(<TaskPreviewModal taskId="parent-a" outputUrl="/original.mp4" onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: '原片' }));
    mocks.task = { ...mocks.task, id: 'parent-b' };
    view.rerender(<TaskPreviewModal taskId="parent-b" outputUrl="/other.mp4" onClose={vi.fn()} />);
    expect(screen.getByLabelText('成片：测试成片')).toHaveAttribute('src', '/erased.mp4');
  });

  it('失败时显示原因，重试派生任务而不是再次创建', () => {
    mocks.task.subtitleErase = { id: 'child-a', status: 'failed', progress: 0, errorMessage: '视频审核失败' };
    render(<TaskPreviewModal taskId="parent-a" outputUrl="/original.mp4" onClose={vi.fn()} />);
    expect(screen.getByText(/视频审核失败/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试去字幕' }));
    expect(mocks.retry).toHaveBeenCalledWith({ id: 'child-a', confirmCharged: false }, expect.any(Object));
    expect(mocks.create).not.toHaveBeenCalled();
  });

  it('主任务失败且可能已扣费时，按结构化 details 提示核对再带 confirmCharged=true 重试', () => {
    mocks.retry.mockReset();
    mocks.retry.mockImplementationOnce((_input: unknown, opts?: { onError?: (err: Error) => void }) => {
      opts?.onError?.(
        new ApiError('请核对供应商任务状态', 40900, {
          reason: 'confirm_charged_required',
          confirmChargedRequired: true,
        }),
      );
    });
    mocks.task = { ...mocks.task, status: 'failed', subtitleErase: null };
    render(<TaskPreviewModal taskId="parent-a" outputUrl={null} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: '重新生成' }));
    expect(mocks.retry).toHaveBeenNthCalledWith(
      1,
      { id: 'parent-a', confirmCharged: false },
      expect.any(Object),
    );
    fireEvent.click(screen.getByRole('button', { name: '已核对，仍要重新生成' }));
    expect(mocks.retry).toHaveBeenNthCalledWith(
      2,
      { id: 'parent-a', confirmCharged: true },
      expect.any(Object),
    );
  });
});
