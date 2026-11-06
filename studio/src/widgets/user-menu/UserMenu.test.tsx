import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { Boxes } from 'lucide-react';
import { UserMenuItems } from '@/features/auth/UserMenuItems';
import { AccessProvider } from '@/shared/access/AccessProvider';
import { GUEST_SUBJECT } from '@/shared/access/types';
import { useSessionStore } from '@/shared/access/session';
import { RegistryProvider } from '@/shared/registry/RegistryProvider';
import { createRegistry } from '@/shared/registry/create-registry';
import { defineModule } from '@/shared/registry/define-module';
import { UserMenu } from './UserMenu';

/* 菜单项来自 auth 模块的真实实现（含两个弹窗），只把取数层换成固定数据 */
vi.mock('@/entities/session/api/session.queries', () => ({
  sessionKeys: { all: ['identity'], me: ['identity', 'me'] },
  useMe: () => ({
    data: {
      id: 'u_1',
      username: 'admin',
      displayName: '老大',
      email: 'boss@example.com',
      avatarUrl: null,
      isActive: true,
      lastLoginAt: null,
      createdAt: '2026-01-01T00:00:00.000Z',
      roles: [{ id: 'r_admin', code: 'admin', name: '超级管理员', isSuperuser: true }],
      permissions: ['*'],
    },
  }),
  useLogin: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpdateProfile: () => ({ mutate: vi.fn(), isPending: false }),
  useChangePassword: () => ({ mutate: vi.fn(), isPending: false }),
}));

const load = () => Promise.resolve({ default: () => null });

const registry = createRegistry([
  defineModule({
    id: 'auth',
    routes: [{ path: 'login', layout: 'bare', load }],
    slots: [{ name: 'user-menu.items', component: UserMenuItems }],
  }),
  defineModule({
    id: 'tasks',
    routes: [
      { path: 'tasks', load, nav: { label: '任务', icon: Boxes, group: '创作' } },
    ],
  }),
]);

const SUBJECT = { id: 'u_1', name: '老大', roles: ['admin'], permissions: ['*'] };

function renderMenu() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/tasks']}>
        <AccessProvider subject={SUBJECT}>
          <RegistryProvider registry={registry}>
            <Routes>
              <Route path="/tasks" element={<UserMenu />} />
              <Route path="/login" element={<div>登录页</div>} />
            </Routes>
          </RegistryProvider>
        </AccessProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('UserMenu', () => {
  beforeEach(() => {
    useSessionStore.setState({ status: 'authenticated', token: 'tk', subject: SUBJECT });
  });

  it('默认收起，点击后才渲染菜单项', async () => {
    const user = userEvent.setup();
    renderMenu();

    expect(screen.queryByText('个人资料')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /账号菜单/ }));

    expect(screen.getByText('个人资料')).toBeInTheDocument();
    expect(screen.getByText('修改密码')).toBeInTheDocument();
    expect(screen.getByText('退出登录')).toBeInTheDocument();
  });

  it('展示昵称、登录名与角色名', async () => {
    const user = userEvent.setup();
    renderMenu();

    await user.click(screen.getByRole('button', { name: /账号菜单/ }));

    expect(screen.getByText('老大')).toBeInTheDocument();
    expect(screen.getByText(/@admin · 超级管理员/)).toBeInTheDocument();
  });

  it('aria-expanded 跟随展开状态', async () => {
    const user = userEvent.setup();
    renderMenu();

    const trigger = screen.getByRole('button', { name: /账号菜单/ });
    expect(trigger).toHaveAttribute('aria-expanded', 'false');

    await user.click(trigger);
    expect(trigger).toHaveAttribute('aria-expanded', 'true');

    await user.keyboard('{Escape}');
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
  });

  it('点击菜单项打开弹窗时，下拉不因 portal 点击而关闭', async () => {
    const user = userEvent.setup();
    renderMenu();

    await user.click(screen.getByRole('button', { name: /账号菜单/ }));
    await user.click(screen.getByText('个人资料'));

    // 弹窗出现，且菜单仍在（否则弹窗会随组件卸载一起消失）
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /账号菜单/ })).toHaveAttribute(
      'aria-expanded',
      'true',
    );
  });

  it('退出登录会清空会话并回到登录页', async () => {
    const user = userEvent.setup();
    renderMenu();

    await user.click(screen.getByRole('button', { name: /账号菜单/ }));
    await user.click(screen.getByText('退出登录'));

    expect(await screen.findByText('登录页')).toBeInTheDocument();
    expect(useSessionStore.getState().status).toBe('anonymous');
    expect(useSessionStore.getState().subject).toEqual(GUEST_SUBJECT);
  });
});
