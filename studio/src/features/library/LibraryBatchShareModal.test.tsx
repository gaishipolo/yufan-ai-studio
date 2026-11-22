import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { LibraryBatchShareModal } from './LibraryBatchShareModal';

const { toastMock } = vi.hoisted(() => ({ toastMock: vi.fn() }));
vi.mock('@/shared/lib/toast', () => ({ toast: toastMock }));

const GROUPS = [
  { id: 'g1', name: '一组' },
  { id: 'g2', name: '二组' },
];

beforeEach(() => toastMock.mockReset());

describe('LibraryBatchShareModal · 一批素材共享到一个目标', () => {
  it('默认团队，提交带上目标与理由', async () => {
    const user = userEvent.setup();
    const submit = vi.fn();
    render(
      <LibraryBatchShareModal
        count={3}
        groups={GROUPS}
        canDirect
        allOwned
        onClose={vi.fn()}
        onSubmit={submit}
      />,
    );

    expect(screen.getByText('共享到… · 已选 3 项')).toBeInTheDocument();
    await user.type(screen.getByPlaceholderText('可选：说明这批素材的用途'), '这批给全员看');
    await user.click(screen.getByRole('button', { name: '提交共享' }));

    expect(submit).toHaveBeenCalledWith({ target: 'team', groupId: undefined, reason: '这批给全员看' });
  });

  it('选了小组就把 groupId 交给上层去批量提交', async () => {
    const user = userEvent.setup();
    const submit = vi.fn();
    render(
      <LibraryBatchShareModal
        count={2}
        groups={GROUPS}
        canDirect
        allOwned
        onClose={vi.fn()}
        onSubmit={submit}
      />,
    );

    await user.click(screen.getByRole('radio', { name: '小组 · 二组' }));
    await user.click(screen.getByRole('button', { name: '提交共享' }));

    expect(submit).toHaveBeenCalledWith({ target: 'group', groupId: 'g2', reason: '' });
  });

  it('整批里有别人的素材就标「需审批」，不谎称立即生效', () => {
    render(
      <LibraryBatchShareModal
        count={4}
        groups={GROUPS}
        canDirect
        allOwned={false}
        onClose={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );
    expect(screen.getByText('需审批')).toBeInTheDocument();
    expect(screen.queryByText('立即生效')).not.toBeInTheDocument();
  });

  it('没有直接共享权限时，即使整批都是我的也要审批', () => {
    render(
      <LibraryBatchShareModal
        count={2}
        groups={GROUPS}
        canDirect={false}
        allOwned
        onClose={vi.fn()}
        onSubmit={vi.fn()}
      />,
    );
    expect(screen.getByText('需审批')).toBeInTheDocument();
  });
});
