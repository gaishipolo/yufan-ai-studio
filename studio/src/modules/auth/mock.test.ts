import { AxiosHeaders, type AxiosError, type InternalAxiosRequestConfig } from 'axios';
import { describe, expect, it } from 'vitest';
import { mockAdapter } from '@/mocks/adapter';
import type { ApiResponse } from '@/shared/api/types';
import type { Me } from '@/entities/user/model/types';
import type { TokenResult } from '@/entities/session/model/types';

interface MockResult<T> {
  status: number;
  body: ApiResponse<T>;
}

/** 直接喂 axios 配置给 adapter；带 token 的用例靠 Authorization 头区分身份 */
async function call<T>(
  method: string,
  url: string,
  options: { params?: Record<string, unknown>; data?: unknown; token?: string } = {},
): Promise<MockResult<T>> {
  const headers = new AxiosHeaders();
  if (options.token) headers.set('Authorization', `Bearer ${options.token}`);

  const config = {
    url,
    method,
    params: options.params,
    data: options.data === undefined ? undefined : JSON.stringify(options.data),
    headers,
  } as unknown as InternalAxiosRequestConfig;

  try {
    const response = await mockAdapter(config);
    return { status: response.status, body: response.data as ApiResponse<T> };
  } catch (error) {
    const axiosError = error as AxiosError<ApiResponse<T>>;
    return {
      status: axiosError.response?.status ?? 500,
      body: axiosError.response?.data as ApiResponse<T>,
    };
  }
}

const login = (username: string, password: string) =>
  call<TokenResult>('post', '/identity/login', { data: { username, password } });

const loginOk = async (username: string, password: string) => {
  const result = await login(username, password);
  expect(result.status).toBe(200);
  return result.body.data as TokenResult;
};

describe('auth mock · 登录', () => {
  it('凭证正确时返回令牌与完整身份（含 permissions）', async () => {
    const result = await loginOk('admin', 'admin@12345');

    expect(result.accessToken).toBeTruthy();
    expect(result.tokenType).toBe('Bearer');
    expect(result.expiresIn).toBeGreaterThan(0);
    expect(result.user.username).toBe('admin');
    expect(result.user.permissions).toEqual(['*']);
    expect(result.user.roles[0]?.code).toBe('admin');
  });

  it('用户名大小写与前后空格会被归一化', async () => {
    const result = await login('  ADMIN ', 'admin@12345');
    expect(result.status).toBe(200);
    expect(result.body.data?.user.username).toBe('admin');
  });

  it('密码错误与用户不存在返回同一句提示（不泄露账号是否存在）', async () => {
    const wrongPassword = await login('admin', 'nope');
    const missingUser = await login('ghost', 'nope');

    expect(wrongPassword.status).toBe(401);
    expect(missingUser.status).toBe(401);
    expect(wrongPassword.body.message).toBe(missingUser.body.message);
  });

  it('空用户名或空密码返回 422', async () => {
    expect((await login('', 'x')).status).toBe(422);
    expect((await login('admin', '')).status).toBe(422);
  });

  it('连续输错到第 6 次才 429（前 5 次仍是"密码错误"）', async () => {
    // 用一个不存在的用户名，避免污染种子账号在其它用例里的计数器
    const victim = 'throttle-probe@none';
    const statuses: number[] = [];
    for (let i = 0; i < 6; i += 1) statuses.push((await login(victim, 'wrong')).status);

    expect(statuses.slice(0, 5)).toEqual([401, 401, 401, 401, 401]);
    expect(statuses[5]).toBe(429);
  });
});

describe('auth mock · /me', () => {
  it('没有令牌返回 401', async () => {
    const result = await call<Me>('get', '/identity/me');
    expect(result.status).toBe(401);
  });

  it('令牌无效返回 401', async () => {
    const result = await call<Me>('get', '/identity/me', { token: 'mock-token:ghost:1' });
    expect(result.status).toBe(401);
  });

  it('带着登录拿到的令牌能取回自己', async () => {
    const { accessToken, user } = await loginOk('editor', 'editor@12345');
    const result = await call<Me>('get', '/identity/me', { token: accessToken });

    expect(result.status).toBe(200);
    expect(result.body.data?.id).toBe(user.id);
    expect(result.body.data?.username).toBe('editor');
  });
});

describe('auth mock · 修改本人资料', () => {
  it('只改传了的字段，未传的保持不变', async () => {
    const { accessToken, user } = await loginOk('editor', 'editor@12345');
    const before = user.email;

    const patched = await call<Me>('patch', '/identity/me', {
      token: accessToken,
      data: { displayName: '新的昵称' },
    });

    expect(patched.status).toBe(200);
    expect(patched.body.data?.displayName).toBe('新的昵称');
    expect(patched.body.data?.email).toBe(before);

    // 落盘了：再取一次还是新名字
    const again = await call<Me>('get', '/identity/me', { token: accessToken });
    expect(again.body.data?.displayName).toBe('新的昵称');
  });

  it('邮箱传 null 表示清空', async () => {
    const { accessToken } = await loginOk('editor', 'editor@12345');
    const cleared = await call<Me>('patch', '/identity/me', {
      token: accessToken,
      data: { email: null },
    });

    expect(cleared.body.data?.email).toBeNull();
  });

  it('邮箱格式不合法返回 422', async () => {
    const { accessToken } = await loginOk('editor', 'editor@12345');
    const result = await call<Me>('patch', '/identity/me', {
      token: accessToken,
      data: { email: 'not-an-email' },
    });

    expect(result.status).toBe(422);
  });
});

describe('auth mock · 修改自己的密码', () => {
  const changePassword = (token: string, data: Record<string, unknown>) =>
    call<null>('post', '/identity/me/password', { token, data });

  it('旧密码不正确返回 400', async () => {
    const { accessToken } = await loginOk('viewer', 'viewer@12345');
    const result = await changePassword(accessToken, {
      oldPassword: 'wrong-password',
      newPassword: 'Viewer@54321',
    });

    expect(result.status).toBe(400);
  });

  it('新密码过短 / 纯数字 / 与旧相同都会被拒绝', async () => {
    const { accessToken } = await loginOk('viewer', 'viewer@12345');

    expect((await changePassword(accessToken, { oldPassword: 'viewer@12345', newPassword: 'short' })).status).toBe(422);
    expect(
      (await changePassword(accessToken, { oldPassword: 'viewer@12345', newPassword: '1234567890123' })).status,
    ).toBe(422);
    expect(
      (await changePassword(accessToken, { oldPassword: 'viewer@12345', newPassword: 'viewer@12345' })).status,
    ).toBe(422);
  });

  it('改成功后新密码可登录、旧密码失效（用例末尾改回，避免污染其它用例）', async () => {
    const { accessToken } = await loginOk('viewer', 'viewer@12345');

    const changed = await changePassword(accessToken, {
      oldPassword: 'viewer@12345',
      newPassword: 'Viewer@2026x',
    });
    expect(changed.status).toBe(200);

    expect((await login('viewer', 'viewer@12345')).status).toBe(401);
    const relogin = await loginOk('viewer', 'Viewer@2026x');

    // 改回去
    const restored = await changePassword(relogin.accessToken, {
      oldPassword: 'Viewer@2026x',
      newPassword: 'viewer@12345',
    });
    expect(restored.status).toBe(200);
    expect((await login('viewer', 'viewer@12345')).status).toBe(200);
  });
});
