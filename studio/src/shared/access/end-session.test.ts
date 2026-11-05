import { beforeEach, describe, expect, it, vi } from 'vitest';
import { queryClient } from '@/shared/api/query-client';
import { GUEST_SUBJECT } from './types';
import { useSessionStore } from './session';
import { endSession } from './end-session';

const requestLogout = vi.hoisted(() => vi.fn().mockResolvedValue(undefined));

vi.mock('@/shared/api/auth', () => ({ requestLogout, requestRefresh: vi.fn() }));

const subject = { id: 'u_1', name: '老大', roles: ['admin'], permissions: ['*'] };

describe('endSession', () => {
  beforeEach(() => {
    queryClient.clear();
    localStorage.clear();
    // vitest 的 `restoreMocks: true` 会把 mock 的实现也还原掉，所以每个用例重新给
    requestLogout.mockReset().mockResolvedValue(undefined);
    useSessionStore.setState({
      status: 'authenticated',
      subject,
      token: 'tk',
      expiresAt: Date.now() + 60_000,
      signOutReason: null,
    });
    // 模拟"上一个账号的数据还躺在缓存里"
    queryClient.setQueryData(['images', 'list', 1], { items: [{ id: 'img_1' }], total: 1 });
    queryClient.setQueryData(['identity', 'roles'], [{ id: 'r_1' }]);
  });

  it('清会话的同时清查询缓存', () => {
    endSession('unauthorized');

    expect(useSessionStore.getState().status).toBe('anonymous');
    expect(useSessionStore.getState().token).toBeNull();
    expect(useSessionStore.getState().signOutReason?.kind).toBe('unauthorized');
    // 关键断言：下个账号登录后首屏不能再看到这份数据
    expect(queryClient.getQueryData(['images', 'list', 1])).toBeUndefined();
    expect(queryClient.getQueryData(['identity', 'roles'])).toBeUndefined();
  });

  it('默认原因是 manual，且重复调用不会有副作用', () => {
    endSession();
    expect(useSessionStore.getState().signOutReason?.kind).toBe('manual');

    expect(() => endSession('expired')).not.toThrow();
    expect(useSessionStore.getState().signOutReason?.kind).toBe('expired');
    expect(useSessionStore.getState().subject).toEqual(GUEST_SUBJECT);
  });

  it('四条结束路径都能复用（过期 / 401 / 他页退出 / 手动）', () => {
    for (const reason of ['expired', 'unauthorized', 'external', 'manual'] as const) {
      useSessionStore.setState({ status: 'authenticated', token: 'tk', subject });
      queryClient.setQueryData(['tasks', 'list'], { items: [] });

      endSession(reason);

      expect(useSessionStore.getState().signOutReason?.kind).toBe(reason);
      expect(queryClient.getQueryData(['tasks', 'list'])).toBeUndefined();
    }
  });

  /* FE3-01：以前「退出登录」只清本地，服务端那条会话还活着（2 小时内有效），
     共用电脑上点退出等于什么都没做。只有手动退出需要真的吊销。 */
  it('手动退出会通知服务端作废会话，且带的是清场前那张令牌', () => {
    endSession('manual');

    expect(requestLogout).toHaveBeenCalledWith('tk');
    expect(useSessionStore.getState().token).toBeNull();
  });

  it('过期 / 401 / 他页退出不再调后端（Session 已经不可用，没有可吊销的东西）', () => {
    for (const reason of ['expired', 'unauthorized', 'external'] as const) {
      useSessionStore.setState({ status: 'authenticated', token: 'tk', subject });
      endSession(reason);
    }
    expect(requestLogout).not.toHaveBeenCalled();
  });

  it('吊销请求失败不会把用户卡在页面里（fire-and-forget）', () => {
    requestLogout.mockRejectedValueOnce(new Error('网络断了'));

    expect(() => endSession('manual')).not.toThrow();
    expect(useSessionStore.getState().status).toBe('anonymous');
  });
});
