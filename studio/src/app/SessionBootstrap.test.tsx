import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '@/shared/api/types';
import { GUEST_SUBJECT } from '@/shared/access/types';
import { useSessionStore } from '@/shared/access/session';
import type { Me } from '@/entities/user/model/types';
import { SessionBootstrap } from './SessionBootstrap';

/** `/identity/me` 的替身：由每个用例决定这次引导是成功、401 还是 500 */
const meMock = vi.hoisted(() => vi.fn());

/** 换票的替身。真实实现会写 store，替身也要写，否则组件看不出「票到手了」 */
const refreshMock = vi.hoisted(() => vi.fn());

vi.mock('@/entities/session/api/session.api', () => ({
  sessionApi: {
    me: meMock,
    login: vi.fn(),
    updateProfile: vi.fn(),
    changePassword: vi.fn(),
  },
}));

vi.mock('@/shared/access/token-refresh', () => ({
  refreshAccessToken: refreshMock,
}));

/* 退出登录会 fire-and-forget 打 `POST /identity/logout`（旁路通道，不走拦截器）。
   用例里没后端，那发请求会变成 jsdom 的 XHR 噪音 —— 直接替身掉。

   刻意写成普通函数而不是 `vi.fn()`：`restoreMocks: true` 会把 vi.fn 的实现
   也还原成「返回 undefined」，而 `endSession` 会对返回值调 `.catch`。 */
vi.mock('@/shared/api/auth', () => ({
  requestRefresh: vi.fn(),
  requestLogout: () => Promise.resolve(),
}));

const ME = {
  id: 'u_1',
  username: 'admin',
  displayName: '管理员',
  email: null,
  avatarUrl: null,
  isActive: true,
  lastLoginAt: null,
  createdAt: '2026-01-01T00:00:00.000Z',
  roles: [{ id: 'r_1', code: 'admin', name: '超级管理员' }],
  permissions: ['*'],
} as unknown as Me;

function renderBootstrap() {
  // retryDelay: 0 —— 只关心「失败会不会被说出来」，不想为一次退避等 1 秒
  const client = new QueryClient({ defaultOptions: { queries: { retryDelay: 0 } } });
  return render(
    <QueryClientProvider client={client}>
      <SessionBootstrap>
        <div>受保护内容</div>
      </SessionBootstrap>
    </QueryClientProvider>,
  );
}

describe('SessionBootstrap · 引导失败的出口', () => {
  beforeEach(() => {
    meMock.mockReset();
    useSessionStore.setState({
      // 只有令牌、没有身份快照 → 刷新后停在 loading，等 /me 定性
      status: 'loading',
      subject: GUEST_SUBJECT,
      token: 'stale-token',
      expiresAt: null,
      signOutReason: null,
    });
  });

  /* 这条是 FE2-01 的回归用例：以前 /me 非 401 失败时 status 永远停在 loading，
     <RequireAuth> 一直转圈，用户只能手动清 localStorage。 */
  it('/me 非 401 失败时离开 loading，给出重试与重新登录', async () => {
    meMock.mockRejectedValue(new ApiError('服务端出错了', 50000));

    renderBootstrap();

    expect(await screen.findByText('无法恢复登录状态')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重新登录' })).toBeInTheDocument();
    expect(screen.queryByText('受保护内容')).not.toBeInTheDocument();
  });

  it('重试成功后回到正常渲染', async () => {
    // 持续失败才会停在恢复页（单次抖动会被 shouldRetry 自动重试掉）
    meMock.mockRejectedValue(new ApiError('请求超时', 408));

    renderBootstrap();

    const retryButton = await screen.findByRole('button', { name: '重试' });
    meMock.mockResolvedValue(ME);
    await userEvent.click(retryButton);

    expect(await screen.findByText('受保护内容')).toBeInTheDocument();
    expect(useSessionStore.getState().status).toBe('authenticated');
  });

  it('重新登录会结束会话（守卫随后把人送去登录页）', async () => {
    meMock.mockRejectedValue(new ApiError('连接不上服务', -1));

    renderBootstrap();

    await userEvent.click(await screen.findByRole('button', { name: '重新登录' }));

    expect(useSessionStore.getState().status).toBe('anonymous');
    expect(useSessionStore.getState().token).toBeNull();
  });

  it('401 由拦截器清场，不显示「恢复失败」', async () => {
    meMock.mockRejectedValue(new ApiError('登录状态已失效', 40121));
    useSessionStore.setState({ status: 'anonymous', token: null });

    renderBootstrap();

    expect(screen.getByText('受保护内容')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    await waitFor(() => expect(meMock).not.toHaveBeenCalled());
  });
});

/**
 * FE4-01 的回归用例组：冷启动时 access 已过期、只剩刷新令牌。
 *
 * 以前启动恢复会把刷新令牌一起删掉，于是「关掉页面超过 2 小时」= 直接回登录页。
 * 更隐蔽的坑在引导这一步：`useMe(Boolean(token))` 的 enabled 是 false，
 * 查询永远不发，status 停在 loading —— 页面会无声地卡在整页 loading。
 */
describe('SessionBootstrap · 冷启动先换票（只剩余刷新令牌）', () => {
  beforeEach(() => {
    meMock.mockReset();
    refreshMock.mockReset();
    localStorage.clear();
    useSessionStore.setState({
      status: 'loading',
      subject: GUEST_SUBJECT,
      token: null,
      expiresAt: null,
      refreshToken: 'rt',
      refreshExpiresAt: Date.now() + 6 * 24 * 60 * 60_000,
      signOutReason: null,
    });
  });

  it('有刷新令牌就先换票，换到后正常渲染', async () => {
    refreshMock.mockImplementation(async () => {
      useSessionStore.getState().applyRefreshedTokens({
        token: 'fresh-token',
        expiresIn: 7_200,
        refreshToken: 'rt2',
        refreshExpiresIn: 604_800,
      });
      return 'fresh-token';
    });
    meMock.mockResolvedValue(ME);

    renderBootstrap();

    expect(await screen.findByText('受保护内容')).toBeInTheDocument();
    expect(refreshMock).toHaveBeenCalledTimes(1);
    // 子节点在 loading 阶段就会渲染，所以"换票成功"要等 /me 回写身份才能断言
    await waitFor(() => expect(useSessionStore.getState().status).toBe('authenticated'));
    expect(useSessionStore.getState().token).toBe('fresh-token');
    // 换票成功之后才允许发 /me —— 顺序反了会拿着过期令牌打一发必 401 的请求
    expect(meMock).toHaveBeenCalledTimes(1);
  });

  it('换票 401（刷新令牌过期 / 被踢）→ 结束会话，原因记为已过期', async () => {
    refreshMock.mockRejectedValue(new ApiError('刷新令牌已过期，请重新登录', 40122));

    renderBootstrap();

    await waitFor(() => expect(useSessionStore.getState().status).toBe('anonymous'));
    expect(useSessionStore.getState().signOutReason?.kind).toBe('expired');
    expect(meMock).not.toHaveBeenCalled();
  });

  it('换票遇到网络故障 → 不踢人，给出可重试的出口', async () => {
    refreshMock.mockRejectedValue(new ApiError('连接不上服务', -1));

    renderBootstrap();

    expect(await screen.findByText('无法恢复登录状态')).toBeInTheDocument();
    // 网络故障不是"会话结束"：本地凭证还在，重试才有意义
    expect(useSessionStore.getState().refreshToken).toBe('rt');
    expect(useSessionStore.getState().status).toBe('loading');
  });

  it('重试换票成功后回到正常渲染', async () => {
    refreshMock.mockRejectedValue(new ApiError('请求超时', 408));

    renderBootstrap();

    const retry = await screen.findByRole('button', { name: '重试' });
    refreshMock.mockImplementation(async () => {
      useSessionStore.getState().applyRefreshedTokens({ token: 'fresh-token', expiresIn: 7_200 });
      return 'fresh-token';
    });
    meMock.mockResolvedValue(ME);
    await userEvent.click(retry);

    expect(await screen.findByText('受保护内容')).toBeInTheDocument();
    expect(useSessionStore.getState().status).toBe('authenticated');
  });

  it('没有刷新令牌时不发换票（旧版本存的会话，只能回登录页）', async () => {
    useSessionStore.setState({ status: 'anonymous', token: null, refreshToken: null });

    renderBootstrap();

    expect(screen.getByText('受保护内容')).toBeInTheDocument();
    await waitFor(() => expect(refreshMock).not.toHaveBeenCalled());
  });
});
