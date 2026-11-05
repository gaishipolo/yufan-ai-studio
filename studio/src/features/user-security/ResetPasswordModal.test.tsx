import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { User } from '@/entities/user/model/types';
import { ResetPasswordModal } from './ResetPasswordModal';

const reset = vi.hoisted(() => vi.fn());

vi.mock('@/entities/user/api/user.queries', () => ({
  useResetPassword: () => ({ mutate: reset, isPending: false }),
}));

const TARGET: User = {
  id: 'u_9',
  username: 'viewer',
  displayName: '只读同学',
  email: null,
  avatarUrl: null,
  isActive: true,
  lastLoginAt: null,
  createdAt: '2026-01-01T00:00:00.000Z',
  roles: [],
};

const renderModal = () =>
  render(<ResetPasswordModal open user={TARGET} onClose={vi.fn()} />);

const submit = async (user: ReturnType<typeof userEvent.setup>) => {
  await user.click(screen.getByRole('button', { name: '重置' }));
};

describe('ResetPasswordModal', () => {
  beforeEach(() => {
    reset.mockReset();
  });

  it('过短的口令不发请求', async () => {
    const user = userEvent.setup();
    renderModal();

    await user.type(screen.getByLabelText(/^新密码/), 'short');
    await user.type(screen.getByLabelText(/确认新密码/), 'short');
    await submit(user);

    expect(screen.getByRole('alert')).toHaveTextContent('新密码至少 10 位');
    expect(reset).not.toHaveBeenCalled();
  });

  it('纯数字口令不发请求', async () => {
    const user = userEvent.setup();
    renderModal();

    await user.type(screen.getByLabelText(/^新密码/), '1234567890');
    await user.type(screen.getByLabelText(/确认新密码/), '1234567890');
    await submit(user);

    expect(screen.getByRole('alert')).toHaveTextContent('新密码不能是纯数字');
    expect(reset).not.toHaveBeenCalled();
  });

  it('两次输入不一致时不发请求', async () => {
    const user = userEvent.setup();
    renderModal();

    await user.type(screen.getByLabelText(/^新密码/), 'viewer12345');
    await user.type(screen.getByLabelText(/确认新密码/), 'viewer12346');
    await submit(user);

    expect(screen.getByRole('alert')).toHaveTextContent('两次输入不一致');
    expect(reset).not.toHaveBeenCalled();
  });

  it('合法时按后端字段名提交 newPassword（写错成 password 会被后端 422）', async () => {
    const user = userEvent.setup();
    renderModal();

    await user.type(screen.getByLabelText(/^新密码/), 'viewer12345');
    await user.type(screen.getByLabelText(/确认新密码/), 'viewer12345');
    await submit(user);

    expect(reset).toHaveBeenCalledTimes(1);
    expect(reset.mock.calls[0][0]).toEqual({ id: 'u_9', newPassword: 'viewer12345' });
  });
});
