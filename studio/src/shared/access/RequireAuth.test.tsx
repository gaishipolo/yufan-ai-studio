import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it } from 'vitest';
import { GUEST_SUBJECT } from './types';
import { SIGN_OUT_REASON_TTL_MS, useSessionStore } from './session';
import { RequireAuth } from './RequireAuth';

/** 登录页探针：把守卫带过来的 from / reason 显示出来，验证「登录后原路返回」与过期提示的依据 */
function LoginProbe() {
  const location = useLocation();
  const state = location.state as { from?: string; reason?: string } | null;
  return (
    <div>
      登录页 from={state?.from ?? '无'} reason={state?.reason ?? '无'}
    </div>
  );
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path="/tasks"
          element={
            <RequireAuth>
              <div>受保护内容</div>
            </RequireAuth>
          }
        />
        <Route path="/login" element={<LoginProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('RequireAuth', () => {
  beforeEach(() => {
    useSessionStore.setState({
      status: 'anonymous',
      subject: GUEST_SUBJECT,
      token: null,
      expiresAt: null,
      signOutReason: null,
    });
  });

  it('加载中显示整页 loading，不渲染受保护内容', () => {
    useSessionStore.setState({ status: 'loading' });
    renderAt('/tasks');

    expect(screen.getByRole('status')).toHaveTextContent('正在恢复登录状态');
    expect(screen.queryByText('受保护内容')).not.toBeInTheDocument();
  });

  it('未登录重定向到登录页，并带上原路径与查询串', () => {
    renderAt('/tasks?status=running');

    expect(screen.getByText('登录页 from=/tasks?status=running reason=无')).toBeInTheDocument();
    expect(screen.queryByText('受保护内容')).not.toBeInTheDocument();
  });

  it('会话刚过期 → 把 expired 一起带过去，登录页才能解释原因', () => {
    useSessionStore.setState({ signOutReason: { kind: 'expired', at: Date.now() } });
    renderAt('/tasks');

    expect(screen.getByText(/reason=expired/)).toBeInTheDocument();
  });

  it('主动退出不传原因（用户自己点的退出，不需要再解释）', () => {
    useSessionStore.setState({ signOutReason: { kind: 'manual', at: Date.now() } });
    renderAt('/tasks');

    expect(screen.getByText(/reason=无/)).toBeInTheDocument();
  });

  it('久违的登出原因不会在下次访问时误报', () => {
    useSessionStore.setState({
      signOutReason: { kind: 'expired', at: Date.now() - SIGN_OUT_REASON_TTL_MS - 1_000 },
    });
    renderAt('/tasks');

    expect(screen.getByText(/reason=无/)).toBeInTheDocument();
  });

  it('已登录渲染受保护内容', () => {
    useSessionStore.setState({
      status: 'authenticated',
      token: 'tk',
      subject: { id: 'u_1', name: '老大', roles: ['admin'], permissions: ['*'] },
    });
    renderAt('/tasks');

    expect(screen.getByText('受保护内容')).toBeInTheDocument();
    expect(screen.queryByText(/登录页/)).not.toBeInTheDocument();
  });
});
