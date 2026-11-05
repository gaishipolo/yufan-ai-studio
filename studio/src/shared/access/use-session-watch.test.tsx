import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { GUEST_SUBJECT } from './types';
import { TOKEN_STORAGE_KEY, useSessionStore } from './session';
import { useSessionCrossTabSync, useSessionExpiryWatch } from './use-session-watch';

const requestRefresh = vi.hoisted(() => vi.fn());

vi.mock('@/shared/api/auth', () => ({
  requestRefresh,
  requestLogout: vi.fn().mockResolvedValue(undefined),
}));

const subject = { id: 'u_1', name: '老大', roles: ['admin'], permissions: ['*'] };

function authenticate(expiresAt: number | null, refreshToken: string | null = null) {
  act(() => {
    useSessionStore.setState({
      status: 'authenticated',
      subject,
      token: 'tk',
      expiresAt,
      refreshToken,
      signOutReason: null,
    });
  });
}

describe('useSessionExpiryWatch', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    localStorage.clear();
  });

  afterEach(() => {
    vi.useRealTimers();
    useSessionStore.setState({
      status: 'anonymous',
      subject: GUEST_SUBJECT,
      token: null,
      expiresAt: null,
      signOutReason: null,
    });
  });

  it('到点自动登出，并留下 expired 原因', () => {
    authenticate(Date.now() + 5_000);
    renderHook(() => useSessionExpiryWatch());

    act(() => {
      vi.advanceTimersByTime(4_000);
    });
    expect(useSessionStore.getState().status).toBe('authenticated');

    act(() => {
      vi.advanceTimersByTime(1_500);
    });
    const state = useSessionStore.getState();
    expect(state.status).toBe('anonymous');
    expect(state.token).toBeNull();
    expect(state.signOutReason?.kind).toBe('expired');
  });

  it('未知到期时刻（后端没给 expiresIn）不会误登出', () => {
    authenticate(null);
    renderHook(() => useSessionExpiryWatch());

    act(() => {
      vi.advanceTimersByTime(60 * 60_000);
    });
    expect(useSessionStore.getState().status).toBe('authenticated');
  });

  it('未登录时不排计时器', () => {
    useSessionStore.setState({ status: 'anonymous', expiresAt: Date.now() + 1_000 });
    renderHook(() => useSessionExpiryWatch());

    act(() => {
      vi.advanceTimersByTime(5_000);
    });
    expect(useSessionStore.getState().status).toBe('anonymous');
    expect(useSessionStore.getState().signOutReason).toBeNull();
  });

  /* FE3-01：后端给了 7 天滑动续期，到点该换票而不是登出。
     否则「每个用户干满 2 小时被踢一次」——服务端本来愿意让他继续待着。 */
  it('有刷新令牌时到点续期，不登出', async () => {
    authenticate(Date.now() + 1_000, 'rt');
    requestRefresh.mockResolvedValue({
      accessToken: 'tk-renewed',
      expiresIn: 7_200,
      refreshToken: 'rt-renewed',
      refreshExpiresIn: 604_800,
    });

    renderHook(() => useSessionExpiryWatch());

    await act(async () => {
      vi.advanceTimersByTime(1_500);
      await Promise.resolve();
    });

    const state = useSessionStore.getState();
    expect(requestRefresh).toHaveBeenCalledWith('rt');
    expect(state.status).toBe('authenticated');
    expect(state.token).toBe('tk-renewed');
    expect(state.refreshToken).toBe('rt-renewed');
    expect(state.signOutReason).toBeNull();
  });

  it('续期失败才登出，并留下 expired 原因', async () => {
    authenticate(Date.now() + 1_000, 'rt');
    requestRefresh.mockRejectedValue(new Error('refresh 被吊销'));

    renderHook(() => useSessionExpiryWatch());

    await act(async () => {
      vi.advanceTimersByTime(1_500);
      await Promise.resolve();
      await Promise.resolve();
    });

    const state = useSessionStore.getState();
    expect(state.status).toBe('anonymous');
    expect(state.signOutReason?.kind).toBe('expired');
  });
});

describe('useSessionCrossTabSync', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('别的标签页删掉令牌 → 本页跟着登出，原因是 external', () => {
    authenticate(Date.now() + 60_000);
    renderHook(() => useSessionCrossTabSync());

    act(() => {
      window.dispatchEvent(
        new StorageEvent('storage', { key: TOKEN_STORAGE_KEY, newValue: null }),
      );
    });

    expect(useSessionStore.getState().status).toBe('anonymous');
    expect(useSessionStore.getState().signOutReason?.kind).toBe('external');
  });

  it('令牌被写成新值（换号）不在这里处理，避免留下上个账号的缓存', () => {
    authenticate(Date.now() + 60_000);
    renderHook(() => useSessionCrossTabSync());

    act(() => {
      window.dispatchEvent(
        new StorageEvent('storage', { key: TOKEN_STORAGE_KEY, newValue: 'other-token' }),
      );
    });

    expect(useSessionStore.getState().status).toBe('authenticated');
  });
});
