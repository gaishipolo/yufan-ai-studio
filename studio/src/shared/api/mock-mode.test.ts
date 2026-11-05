import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/* ------------------------------------------------------------------
 * mock 模式下的认证链路（集成级）
 *
 * 这条用例存在的理由很具体：mock 适配器挂在**主干** axios 实例上，
 * 而续期 / 登出走的是另一条旁路实例。一次改动里漏掉 `authHttp` 的适配器时，
 * 类型检查、lint、其它用例全绿 —— 只有真跑 mock 才会发现「续期直接打到真后端」，
 * 而那时通常是演示现场。
 *
 * 断言覆盖：登录拿到刷新令牌 → 续期轮换 → 登出后旧票失效。
 * 任何一步走了真实网络都会立刻失败（jsdom 里没有后端）。
 * ------------------------------------------------------------------ */

const CREDENTIALS = { username: 'admin', password: 'admin@12345' };

async function loadMockMode() {
  vi.stubEnv('VITE_USE_MOCK', 'true');
  vi.resetModules();
  const http = await import('./http');
  const auth = await import('./auth');
  return { ...http, ...auth };
}

describe('mock 模式 · 登录 / 续期 / 登出', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it('走 mock 适配器完成整条链路（登录 → 续期 → 登出后失效）', async () => {
    const { api, requestRefresh, requestLogout } = await loadMockMode();

    const login = await api.post<{ accessToken: string; refreshToken: string; expiresIn: number }>(
      '/identity/login',
      CREDENTIALS,
    );
    expect(login.accessToken).toBeTruthy();
    expect(login.refreshToken).toBeTruthy();
    // 与后端同口径：2 小时 access + 7 天 refresh
    expect(login.expiresIn).toBe(7_200);

    const renewed = await requestRefresh(login.refreshToken);
    expect(renewed.accessToken).not.toBe(login.accessToken);
    expect(renewed.refreshToken).not.toBe(login.refreshToken);

    // 轮换后旧刷新令牌不能再换东西
    await expect(requestRefresh(login.refreshToken)).rejects.toBeTruthy();

    await expect(requestLogout(renewed.accessToken as string)).resolves.toBeUndefined();

    // 登出后那张 access 立刻失效（mock 也记 revoked，不只是前端删本地）
    await expect(
      api.get('/identity/me', undefined, {
        headers: { Authorization: `Bearer ${renewed.accessToken}` },
      }),
    ).rejects.toBeTruthy();
  });
});
