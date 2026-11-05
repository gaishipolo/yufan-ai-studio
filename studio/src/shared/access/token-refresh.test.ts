import { beforeEach, describe, expect, it, vi } from 'vitest';

/* 续期是「一次只发一个请求」的逻辑，模块里有在飞的 Promise，
   所以每个用例都重新加载模块（与 session.test.ts 同一套办法）。 */
const requestRefresh = vi.hoisted(() => vi.fn());

vi.mock('@/shared/api/auth', () => ({
  requestRefresh,
  requestLogout: vi.fn().mockResolvedValue(undefined),
}));

const subject = { id: 'u_1', name: '老大', roles: ['admin'], permissions: ['*'] };

const TOKENS = {
  accessToken: 'tk-new',
  tokenType: 'Bearer',
  expiresIn: 7_200,
  refreshToken: 'rt-new',
  refreshExpiresIn: 604_800,
};

async function load() {
  vi.resetModules();
  const session = await import('./session');
  const refresh = await import('./token-refresh');
  return { ...session, ...refresh };
}

describe('refreshAccessToken', () => {
  beforeEach(() => {
    localStorage.clear();
    requestRefresh.mockReset();
  });

  it('并发调用共享同一次请求（六个 401 只换一张票）', async () => {
    const { useSessionStore, refreshAccessToken } = await load();
    useSessionStore.getState().signIn({ token: 'tk', refreshToken: 'rt', subject });
    requestRefresh.mockResolvedValue(TOKENS);

    const results = await Promise.all([
      refreshAccessToken(),
      refreshAccessToken(),
      refreshAccessToken(),
    ]);

    expect(requestRefresh).toHaveBeenCalledTimes(1);
    expect(requestRefresh).toHaveBeenCalledWith('rt');
    expect(results).toEqual(['tk-new', 'tk-new', 'tk-new']);
  });

  it('成功后把新票写进 store，身份保持不变', async () => {
    const { useSessionStore, refreshAccessToken } = await load();
    useSessionStore.getState().signIn({ token: 'tk', refreshToken: 'rt', subject });
    requestRefresh.mockResolvedValue(TOKENS);

    await refreshAccessToken();

    const state = useSessionStore.getState();
    expect(state.token).toBe('tk-new');
    expect(state.refreshToken).toBe('rt-new');
    expect(state.subject.id).toBe('u_1');
    expect(state.expiresAt).toBeGreaterThan(Date.now());
  });

  it('没有刷新令牌时直接失败，不发请求（旧版本存的会话只能等 401）', async () => {
    const { useSessionStore, refreshAccessToken } = await load();
    useSessionStore.getState().signIn({ token: 'tk', subject });

    await expect(refreshAccessToken()).rejects.toThrow(/没有刷新令牌/);
    expect(requestRefresh).not.toHaveBeenCalled();
  });

  it('刷新令牌自己过期了也不发请求（注定 401 的请求没必要打）', async () => {
    const { useSessionStore, refreshAccessToken } = await load();
    useSessionStore.getState().signIn({
      token: 'tk',
      refreshToken: 'rt',
      refreshExpiresIn: 1,
      subject,
    });
    useSessionStore.setState({ refreshExpiresAt: Date.now() - 1 });

    await expect(refreshAccessToken()).rejects.toThrow(/刷新令牌已过期/);
    expect(requestRefresh).not.toHaveBeenCalled();
  });

  it('失败时先看别的标签页有没有换过票：换过就跟过去，不清场', async () => {
    const { useSessionStore, refreshAccessToken } = await load();
    useSessionStore.getState().signIn({ token: 'tk', refreshToken: 'rt', subject });
    requestRefresh.mockRejectedValue(new Error('refresh token invalid'));

    // 另一个标签页刚刚续期：存储里已经是新票
    localStorage.setItem('sf_token', 'tk-other-tab');
    localStorage.setItem('sf_token_expires_at', String(Date.now() + 7_200_000));
    localStorage.setItem('sf_refresh_token', 'rt-other-tab');
    localStorage.setItem('sf_subject', JSON.stringify(subject));

    await expect(refreshAccessToken()).resolves.toBe('tk-other-tab');
    expect(useSessionStore.getState().token).toBe('tk-other-tab');
  });

  it('续期期间会话被结束 → 拒绝，不把新票塞回已退出的会话', async () => {
    const { useSessionStore, refreshAccessToken } = await load();
    useSessionStore.getState().signIn({ token: 'tk', refreshToken: 'rt', subject });
    requestRefresh.mockImplementation(async () => {
      // 请求在路上时用户点了退出
      useSessionStore.getState().signOut('manual');
      return TOKENS;
    });

    await expect(refreshAccessToken()).rejects.toThrow(/会话已结束/);
    expect(useSessionStore.getState().status).toBe('anonymous');
    expect(localStorage.getItem('sf_token')).toBeNull();
  });

  it('续期期间换了账号 → 拒绝，旧账号的新票不能覆盖新会话', async () => {
    const { useSessionStore, refreshAccessToken } = await load();
    useSessionStore.getState().signIn({ token: 'tk', refreshToken: 'rt', subject });
    requestRefresh.mockImplementation(async () => {
      // 请求在路上时，另一个人在这个标签页登录了
      useSessionStore.getState().signIn({
        token: 'tk-other-user',
        refreshToken: 'rt-other-user',
        subject: { ...subject, id: 'u_2' },
      });
      return TOKENS;
    });

    await expect(refreshAccessToken()).rejects.toThrow(/会话已结束/);

    const state = useSessionStore.getState();
    expect(state.token).toBe('tk-other-user');
    expect(state.refreshToken).toBe('rt-other-user');
    expect(state.subject.id).toBe('u_2');
  });
});
