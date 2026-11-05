import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios';
import { beforeEach, describe, expect, it, vi } from 'vitest';

/* 拦截器要测的只有一件事：401 之后**先换票再重放**，换不到才清场。
   把适配器换成一个"第一次 401、之后 200"的假服务端，比 mock 掉 axios 更接近真实链路
   （请求拦截器、重放、错误归一都还在路上跑）。 */
const requestRefresh = vi.hoisted(() => vi.fn());

vi.mock('./auth', () => ({
  requestRefresh,
  requestLogout: vi.fn().mockResolvedValue(undefined),
}));

const { http, api } = await import('./http');
const { useSessionStore } = await import('@/shared/access/session');
const { ApiError } = await import('./types');

const subject = { id: 'u_1', name: '老大', roles: ['admin'], permissions: ['*'] };

const TOKENS = { accessToken: 'tk-renewed', expiresIn: 7_200, refreshToken: 'rt-renewed' };

function respond(
  config: InternalAxiosRequestConfig,
  status: number,
  data: unknown,
): AxiosResponse {
  return { data, status, statusText: String(status), headers: {}, config };
}

/** 假服务端：按顺序吐预设的状态码，记录每次请求带的鉴权头 */
function serve(statuses: number[]) {
  const seen: Array<{ url?: string; auth?: string }> = [];
  http.defaults.adapter = async (config) => {
    seen.push({
      url: config.url,
      auth: String(config.headers.Authorization ?? ''),
    });
    const status = statuses.shift() ?? 200;
    if (status >= 400) {
      throw new AxiosError(
        'Unauthorized',
        'ERR_BAD_REQUEST',
        config,
        null,
        respond(config, status, { code: 40100 + status - 400, message: '登录状态已失效' }),
      );
    }
    return respond(config, status, { code: 0, message: 'ok', data: { ok: true } });
  };
  return seen;
}

describe('http 拦截器 · 401 的两级处理', () => {
  beforeEach(() => {
    localStorage.clear();
    requestRefresh.mockReset();
    useSessionStore.getState().signIn({
      token: 'tk-old',
      expiresIn: 7_200,
      refreshToken: 'rt',
      subject,
    });
  });

  it('401 → 换票 → 重放原请求，调用方无感', async () => {
    const seen = serve([401]);
    requestRefresh.mockResolvedValue(TOKENS);

    await expect(api.get('/tasks')).resolves.toEqual({ ok: true });

    expect(requestRefresh).toHaveBeenCalledTimes(1);
    expect(seen).toHaveLength(2);
    // 重放必须带新票，否则会再 401 一次
    expect(seen[1].auth).toBe('Bearer tk-renewed');
    expect(useSessionStore.getState().token).toBe('tk-renewed');
  });

  it('换不到票 → 清场，并把原始 401 抛给调用方', async () => {
    serve([401]);
    requestRefresh.mockRejectedValue(new Error('refresh 被吊销'));

    await expect(api.get('/tasks')).rejects.toBeInstanceOf(ApiError);
    expect(useSessionStore.getState().status).toBe('anonymous');
    expect(useSessionStore.getState().signOutReason?.kind).toBe('unauthorized');
  });

  it('重放后仍然 401：不无限重试，直接清场', async () => {
    const seen = serve([401, 401]);
    requestRefresh.mockResolvedValue(TOKENS);

    await expect(api.get('/tasks')).rejects.toBeInstanceOf(ApiError);
    // 只有两次：原始请求 + 一次重放
    expect(seen).toHaveLength(2);
    expect(requestRefresh).toHaveBeenCalledTimes(1);
    expect(useSessionStore.getState().status).toBe('anonymous');
  });

  it('登录接口的 401 不换票（密码错了不是会话失效）', async () => {
    serve([401]);

    await expect(api.post('/identity/login', { username: 'a', password: 'b' })).rejects.toBeInstanceOf(
      ApiError,
    );
    expect(requestRefresh).not.toHaveBeenCalled();
    expect(useSessionStore.getState().status).toBe('authenticated');
  });

  it('非 401 的失败不碰会话', async () => {
    serve([500]);
    requestRefresh.mockResolvedValue(TOKENS);

    await expect(api.get('/tasks')).rejects.toBeInstanceOf(ApiError);
    expect(requestRefresh).not.toHaveBeenCalled();
    expect(useSessionStore.getState().status).toBe('authenticated');
  });

  /* FE4-09：判定用精确路径，不用 includes。
     `/identity/login-audit` 只是"长得像"，它的 401 是普通会话失效，该续期。 */
  it('形似登录的路径仍然走续期（不被当成密码错误）', async () => {
    const seen = serve([401]);
    requestRefresh.mockResolvedValue(TOKENS);

    await expect(api.get('/identity/login-audit')).resolves.toEqual({ ok: true });

    expect(requestRefresh).toHaveBeenCalledTimes(1);
    expect(seen).toHaveLength(2);
  });

  it('登录路径带查询串时仍认得出是认证入口', async () => {
    serve([401]);

    await expect(api.post('/identity/login?t=1', {})).rejects.toBeInstanceOf(ApiError);

    expect(requestRefresh).not.toHaveBeenCalled();
    expect(useSessionStore.getState().status).toBe('authenticated');
  });
});
