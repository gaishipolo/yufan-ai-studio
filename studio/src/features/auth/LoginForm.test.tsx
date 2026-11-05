import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { sessionApi } from '@/entities/session/api/session.api';
import { GUEST_SUBJECT } from '@/shared/access/types';
import { useSessionStore } from '@/shared/access/session';
import { ApiError } from '@/shared/api/types';
import type { Me } from '@/entities/user/model/types';
import { LoginForm } from './LoginForm';

vi.mock('@/entities/session/api/session.api', () => ({
  sessionApi: {
    login: vi.fn(),
    me: vi.fn(),
    updateProfile: vi.fn(),
    changePassword: vi.fn(),
  },
}));

const ME: Me = {
  id: 'u_1',
  username: 'admin',
  displayName: '老大',
  email: null,
  avatarUrl: null,
  isActive: true,
  lastLoginAt: null,
  createdAt: '2026-01-01T00:00:00.000Z',
  roles: [{ id: 'r_admin', code: 'admin', name: '超级管理员', isSuperuser: true }],
  permissions: ['*'],
};

function renderForm(onSuccess?: () => void) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <LoginForm onSuccess={onSuccess} />
    </QueryClientProvider>,
  );
}

describe('LoginForm', () => {
  beforeEach(() => {
    vi.mocked(sessionApi.login).mockReset();
    // 上一个用例登录成功会把令牌落盘，不清掉会让「未登录」的断言读到旧令牌
    localStorage.clear();
    useSessionStore.setState({ status: 'anonymous', subject: GUEST_SUBJECT, token: null });
  });

  it('空表单不发请求，直接提示必填', async () => {
    const user = userEvent.setup();
    renderForm();

    await user.click(screen.getByRole('button', { name: '登录' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('用户名和密码都需要填写');
    expect(sessionApi.login).not.toHaveBeenCalled();
  });

  it('登录成功后写入会话（令牌 + 身份）并落盘', async () => {
    vi.mocked(sessionApi.login).mockResolvedValue({
      accessToken: 'tk-1',
      tokenType: 'Bearer',
      expiresIn: 3_600,
      user: ME,
    });
    const onSuccess = vi.fn();
    const user = userEvent.setup();
    renderForm(onSuccess);

    await user.type(screen.getByLabelText(/用户名/), 'admin');
    await user.type(screen.getByLabelText(/密码/), 'admin@12345');
    await user.click(screen.getByRole('button', { name: '登录' }));

    expect(sessionApi.login).toHaveBeenCalledWith({ username: 'admin', password: 'admin@12345' });

    const state = useSessionStore.getState();
    expect(state.status).toBe('authenticated');
    expect(state.token).toBe('tk-1');
    expect(state.subject.name).toBe('老大');
    expect(state.subject.permissions).toEqual(['*']);
    expect(localStorage.getItem('sf_token')).toBe('tk-1');
    expect(onSuccess).toHaveBeenCalled();
  });

  it('后端拒绝时展示原始提示，且不写会话', async () => {
    vi.mocked(sessionApi.login).mockRejectedValue(new ApiError('用户名或密码错误', 401));
    const user = userEvent.setup();
    renderForm();

    await user.type(screen.getByLabelText(/用户名/), 'admin');
    await user.type(screen.getByLabelText(/密码/), 'wrong');
    await user.click(screen.getByRole('button', { name: '登录' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('用户名或密码错误');
    expect(useSessionStore.getState().status).toBe('anonymous');
    expect(localStorage.getItem('sf_token')).toBeNull();
  });

  it('被限流（429）后按钮进入倒计时锁定，不再让用户空点', async () => {
    vi.mocked(sessionApi.login).mockRejectedValue(
      new ApiError('密码错误次数过多，请 15 分钟后再试', 42_900),
    );
    const user = userEvent.setup();
    renderForm();

    await user.type(screen.getByLabelText(/用户名/), 'admin');
    await user.type(screen.getByLabelText(/密码/), 'wrong');
    await user.click(screen.getByRole('button', { name: '登录' }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('已被暂时锁定');
    expect(alert).toHaveTextContent('15 分钟');

    // 文案是「最多再等」：后端滑动窗口的真实解锁比这个倒计时更早，
    // 写成「请 X 后再试」是把上限当成承诺
    const locked = screen.getByRole('button', { name: /最多再等 15 分/ });
    expect(locked).toBeDisabled();

    await user.click(locked);
    expect(sessionApi.login).toHaveBeenCalledTimes(1);
  });
});
