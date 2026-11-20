import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { AccessProvider } from '@/shared/access/AccessProvider';
import type { Subject } from '@/shared/access/types';
import type { User } from '@/entities/user/model/types';
import { EffectivePermissionsModal } from './EffectivePermissionsModal';

vi.mock('@/entities/group/api/group.queries', () => ({
  useEffectivePermissions: () => ({
    data: {
      team: [],
      groups: [],
      personal: [],
      roles: ['*'],
      pages: [],
      gatedOff: [],
      effective: ['*'],
    },
    isLoading: false,
    isError: false,
    error: null,
  }),
}));

vi.mock('@/entities/role/api/role.queries', () => ({
  usePermissionCatalog: () => ({
    data: { groups: [] },
    isLoading: false,
    isError: false,
    error: null,
  }),
}));

const SUBJECT: Subject = { id: 'u_me', name: '我', roles: ['admin'], permissions: ['*'] };

const user = (roles: User['roles']): User => ({
  id: 'u_1',
  username: 'alice',
  displayName: 'Alice',
  isActive: true,
  lastLoginAt: null,
  createdAt: '2026-01-01T00:00:00.000Z',
  roles,
});

function wrap(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AccessProvider subject={SUBJECT}>{node}</AccessProvider>
    </QueryClientProvider>,
  );
}

/**
 * 超管的权限恒为 `*`（后端 `resolve_identity` 短路），个人层怎么写都不生效。
 *
 * 回归：角色页把「编辑权限」禁了，用户页的「编辑个人权限」却还能点 ——
 * 两个入口对同一件事给了相反的答案，而写进去的那份配置永远不会被读到。
 */
describe('EffectivePermissionsModal · 超管不提供个人授权', () => {
  it('超管账号：按钮禁用并说明原因', () => {
    wrap(
      <EffectivePermissionsModal
        open
        user={user([
          { id: 'r_1', code: 'admin', name: '超级管理员', isSuperuser: true },
        ])}
        onClose={vi.fn()}
        onEditPersonal={vi.fn()}
      />,
    );

    const button = screen.getByRole('button', { name: '编辑个人权限' });
    expect(button).toBeDisabled();
    expect(button.getAttribute('title')).toContain('超管权限恒为全部');
  });

  it('普通账号仍然能编辑（上面那条不是因为整块被关掉）', () => {
    wrap(
      <EffectivePermissionsModal
        open
        user={user([
          { id: 'r_2', code: 'editor', name: '内容编辑', isSuperuser: false },
        ])}
        onClose={vi.fn()}
        onEditPersonal={vi.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: '编辑个人权限' })).toBeEnabled();
  });
});
