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
  phone: '13800138000',
  position: '超级管理员',
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
  phone: '15576908188',
  position: '编导',
  roles: [{ id: 'r_viewer', code: 'viewer', name: '只读成员', isSuperuser: false }],
};

const mocks = vi.hoisted(() => ({
  users: [] as unknown[],
  update: vi.fn(),
  pending: false,
  variables: undefined as { id: string } | undefined,
  groups: [
    {
      id: 'g_content',
      name: '内容组',
      description: '',
      createdAt: '2026-01-01T00:00:00.000Z',
      permissions: ['library:create'],
      memberIds: ['u_2'],
      memberCount: 1,
    },
  ] as unknown[],
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
  usePermissionCatalog: () => ({ data: { groups: [] }, isLoading: false, isError: false, error: null }),
  useSetRolePermissions: () => ({ mutate: vi.fn(), isPending: false }),
  useCreateRole: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateRole: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteRole: () => ({ mutate: vi.fn(), isPending: false }),
}));

/* 三层授权的 hook 全部替掉：本文件验的是页面装配与按钮级门禁，
   不验网络。遗漏任何一个导出都会让「弹窗虽关着但组件已挂载」的 hook
   去找不存在的 QueryClient，报错点离真正原因很远。 */
vi.mock('@/entities/group/api/group.queries', () => {
  const mutation = () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false });
  return {
    useGroupList: () => ({
      data: mocks.groups,
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    }),
    useGroupMembers: () => ({ data: [], isLoading: false }),
    useMemberCandidates: () => ({
      data: { items: mocks.users, total: mocks.users.length, page: 1, pageSize: 100 },
      isLoading: false,
      isError: false,
      error: null,
    }),
    useTeamPermissions: () => ({ data: [], isLoading: false, isError: false, error: null }),
    useUserPermissions: () => ({ data: [], isLoading: false, isError: false, error: null }),
    useEffectivePermissions: () => ({
      data: { team: [], groups: [], personal: [], roles: [], effective: [] },
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    }),
    useCreateGroup: mutation,
    useUpdateGroup: mutation,
    useDeleteGroup: () => ({ ...mutation(), isPending: false }),
    useSetGroupMembers: mutation,
    useSetGroupPermissions: mutation,
    useSetTeamPermissions: mutation,
    useSetUserPermissions: mutation,
  };
});

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

/** 按昵称取到用户表格里的某一行（左栏的小组也是 li，用昵称区分） */
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

  it('只有 identity:read 时不渲染添加成员、停用，也不渲染分组侧栏', () => {
    renderSection(READER);

    expect(screen.queryByRole('button', { name: /添加成员/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '停用' })).not.toBeInTheDocument();
    expect(screen.queryByText('分组')).not.toBeInTheDocument();
  });

  it('有 identity:create 时出现添加成员入口', () => {
    renderSection();

    expect(screen.getByRole('button', { name: /添加成员/ })).toBeInTheDocument();
  });

  it('截图布局：左栏分组 + 三路搜索 + 手机号与职务列', () => {
    renderSection();

    expect(screen.getByText('分组')).toBeInTheDocument();
    expect(screen.getByText('内容组')).toBeInTheDocument();
    expect(screen.getByRole('searchbox', { name: /按名字搜索/ })).toBeInTheDocument();
    expect(screen.getByRole('searchbox', { name: /按账号搜索/ })).toBeInTheDocument();
    expect(screen.getByRole('searchbox', { name: /按手机号搜索/ })).toBeInTheDocument();

    const other = rowOf('只读同学');
    expect(within(other).getByText('15576908188')).toBeInTheDocument();
    expect(within(other).getByText('编导')).toBeInTheDocument();
  });

  it('只读账号也能看「权限」来源，但不能编辑、分配角色或重置密码', () => {
    renderSection(READER);

    const other = rowOf('只读同学');
    expect(within(other).getByRole('button', { name: '权限' })).toBeInTheDocument();
    expect(within(other).queryByRole('button', { name: '编辑' })).not.toBeInTheDocument();
    expect(within(other).queryByRole('button', { name: '角色' })).not.toBeInTheDocument();
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
