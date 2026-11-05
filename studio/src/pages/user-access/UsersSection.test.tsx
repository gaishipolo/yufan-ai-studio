import { render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { AccessProvider } from '@/shared/access/AccessProvider';
import type { Subject } from '@/shared/access/types';
import type { User } from '@/entities/user/model/types';
import { UsersSection } from './UsersSection';

const SELF: User = {
  id: 'u_1',
  username: 'admin',
  displayName: '老大',
  email: null,
  avatarUrl: null,
  isActive: true,
  lastLoginAt: '2026-11-01T02:00:00.000Z',
  createdAt: '2026-01-01T00:00:00.000Z',
  roles: [{ id: 'r_admin', code: 'admin', name: '超级管理员', isSuperuser: true }],
};

const OTHER: User = {
  ...SELF,
  id: 'u_2',
  username: 'viewer',
  displayName: '只读同学',
  roles: [{ id: 'r_viewer', code: 'viewer', name: '只读成员', isSuperuser: false }],
};

const mocks = vi.hoisted(() => ({
  users: [] as unknown[],
  update: vi.fn(),
  pending: false,
  variables: undefined as { id: string } | undefined,
}));

vi.mock('@/entities/user/api/user.queries', () => ({
  useUserList: () => ({
    data: { items: mocks.users, total: mocks.users.length, page: 1, pageSize: 10 },
    isLoading: false,
    isError: false,
    isFetching: false,
    error: null,
    refetch: vi.fn(),
  }),
  useUpdateUser: () => ({
    mutate: mocks.update,
    isPending: mocks.pending,
    variables: mocks.variables,
  }),
  useCreateUser: () => ({ mutate: vi.fn(), isPending: false }),
  useSetUserRoles: () => ({ mutate: vi.fn(), isPending: false }),
  useResetPassword: () => ({ mutate: vi.fn(), isPending: false }),
  useDeactivateUser: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock('@/entities/role/api/role.queries', () => ({
  useRoleList: () => ({ data: [], isLoading: false }),
  usePermissionCatalog: () => ({ data: { groups: [] }, isLoading: false }),
  useSetRolePermissions: () => ({ mutate: vi.fn(), isPending: false }),
  useCreateRole: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateRole: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteRole: () => ({ mutate: vi.fn(), isPending: false }),
}));

const ADMIN: Subject = { id: 'u_1', name: '老大', roles: ['admin'], permissions: ['*'] };
const READER: Subject = {
  id: 'u_1',
  name: '老大',
  roles: ['viewer'],
  permissions: ['identity:read'],
};

function renderSection(subject: Subject = ADMIN) {
  mocks.users = [SELF, OTHER];
  return render(
    <AccessProvider subject={subject}>
      <UsersSection />
    </AccessProvider>,
  );
}

/** 按昵称取到某一行，避免用「第几个 li」这种和渲染顺序耦合的写法 */
function rowOf(name: string) {
  const row = screen.getAllByRole('listitem').find((item) => item.textContent?.includes(name));
  if (!row) throw new Error(`找不到 ${name} 所在的行`);
  return row;
}

describe('UsersSection', () => {
  it('自己那一行标记「我」，并且不能停用自己', () => {
    renderSection();

    const selfRow = rowOf('老大');
    expect(within(selfRow).getByText('我')).toBeInTheDocument();
    expect(within(selfRow).getByRole('button', { name: '停用' })).toBeDisabled();
  });

  it('别人的停用按钮可用（自己那条才是特例）', () => {
    renderSection();

    expect(within(rowOf('只读同学')).getByRole('button', { name: '停用' })).toBeEnabled();
  });

  it('只有 identity:read 时不渲染新建与停用（按钮级门禁）', () => {
    renderSection(READER);

    expect(screen.queryByRole('button', { name: /新建用户/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '停用' })).not.toBeInTheDocument();
  });

  it('有 identity:create 时出现新建入口', () => {
    renderSection();

    expect(screen.getByRole('button', { name: /新建用户/ })).toBeInTheDocument();
  });

  /* FE4-07：`isPending` 是"这个 mutation 有没有在跑"，不是"这一行在不在跑"。
     不按行判，启用 A 时所有已停用行的按钮会一起转圈。 */
  it('启用某一个账号时，只有那一行在转圈', () => {
    mocks.pending = true;
    mocks.variables = { id: OTHER.id };
    mocks.users = [
      { ...SELF, isActive: false },
      { ...OTHER, isActive: false },
    ];

    render(
      <AccessProvider subject={ADMIN}>
        <UsersSection />
      </AccessProvider>,
    );

    expect(within(rowOf('只读同学')).getByRole('button', { name: '启用' })).toBeDisabled();
    expect(within(rowOf('老大')).getByRole('button', { name: '启用' })).toBeEnabled();

    mocks.pending = false;
    mocks.variables = undefined;
  });
});
