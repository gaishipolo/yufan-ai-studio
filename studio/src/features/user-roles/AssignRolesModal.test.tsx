import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { AccessProvider } from '@/shared/access/AccessProvider';
import type { Subject } from '@/shared/access/types';
import type { User } from '@/entities/user/model/types';
import { AssignRolesModal } from './AssignRolesModal';

vi.mock('@/entities/user/api/user.queries', () => ({
  useSetUserRoles: () => ({ mutate: vi.fn(), isPending: false }),
}));

const SUBJECT: Subject = { id: 'u_1', name: '老大', roles: ['admin'], permissions: ['*'] };

const userWith = (id: string): User => ({
  id,
  username: id === 'u_1' ? 'admin' : 'editor',
  displayName: id === 'u_1' ? '老大' : '小编',
  email: null,
  avatarUrl: null,
  isActive: true,
  lastLoginAt: null,
  createdAt: '2026-01-01T00:00:00.000Z',
  roles: [],
});

const renderModal = (target: User) =>
  render(
    <AccessProvider subject={SUBJECT}>
      <AssignRolesModal open user={target} roles={[]} onClose={vi.fn()} />
    </AccessProvider>,
  );

describe('AssignRolesModal', () => {
  it('改自己的角色时给出降权提示（不拦，转交超管是正常流程）', () => {
    renderModal(userWith('u_1'));

    expect(screen.getByRole('status')).toHaveTextContent('你正在修改自己的角色');
    expect(screen.getByRole('button', { name: '保存' })).toBeEnabled();
  });

  it('改别人的角色不出现该提示', () => {
    renderModal(userWith('u_2'));

    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });
});
