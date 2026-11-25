import { Boxes } from 'lucide-react';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { GUEST_SUBJECT, type Subject } from '@/shared/access/types';
import { useSessionStore } from '@/shared/access/session';
import { AccessProvider } from '@/shared/access/AccessProvider';
import { createRegistry, defineModule } from '@/shared/registry';
import { RegistryProvider } from '@/shared/registry/RegistryProvider';
import type { LoginRedirectState } from '@/shared/access/RequireAuth';
import LoginPage from './LoginPage';

vi.mock('@/features/auth/LoginForm', () => ({
  LoginForm: () => <button type="button">登录表单</button>,
}));

const load = () => Promise.resolve({ default: () => null });
const registry = createRegistry([
  defineModule({
    id: 'task-center',
    routes: [
      {
        path: 'tasks',
        load,
        permissions: ['page:task-center'],
        nav: { label: '任务中心', icon: Boxes, group: '资产与数据', groupOrder: 3, order: 1 },
      },
    ],
  }),
  defineModule({
    id: 'studio',
    routes: [
      {
        path: 'studio',
        load,
        permissions: ['page:studio'],
        nav: { label: '生成工作台', icon: Boxes, group: '创作', groupOrder: 1, order: 1 },
      },
    ],
  }),
  defineModule({
    id: 'image',
    routes: [
      {
        path: 'image',
        load,
        permissions: ['page:image'],
        nav: { label: '生图', icon: Boxes, group: '创作', groupOrder: 1, order: 2 },
      },
    ],
  }),
]);

/** 落地页探针：显示当前路径，等同于 <Navigate> 之后 router 停在哪 */
function LandingProbe() {
  const location = useLocation();
  return <div>落地页={location.pathname}</div>;
}

function renderLogin(path: string, state?: LoginRedirectState) {
  const subject = useSessionStore.getState().subject;
  return render(
    <MemoryRouter initialEntries={[{ pathname: path, state }]}>
      <AccessProvider subject={subject}>
        <RegistryProvider registry={registry}>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="*" element={<LandingProbe />} />
          </Routes>
        </RegistryProvider>
      </AccessProvider>
    </MemoryRouter>,
  );
}

const signInAs = (subject: Subject) =>
  useSessionStore.setState({ status: 'authenticated', token: 'tk', subject });

describe('LoginPage · 登录后落地页', () => {
  beforeEach(() => {
    useSessionStore.setState({ status: 'anonymous', subject: GUEST_SUBJECT, token: null });
  });

  it('只有生图权限的用户落到生图页，而不是无权限的任务中心', () => {
    signInAs({ id: 'u_1', name: '小画', roles: [], permissions: ['page:image'] });
    renderLogin('/login');
    expect(screen.getByText('落地页=/image')).toBeInTheDocument();
  });

  it('超管落到侧栏第一项', () => {
    signInAs({ id: 'u_1', name: '管理员', roles: ['admin'], permissions: ['*'] });
    renderLogin('/login');
    expect(screen.getByText('落地页=/studio')).toBeInTheDocument();
  });

  it('深链接被拦回来的用户仍原路返回，不被第一页覆盖', () => {
    signInAs({ id: 'u_1', name: '小画', roles: [], permissions: ['page:image'] });
    renderLogin('/login', { from: '/tasks?status=running' });
    expect(screen.getByText('落地页=/tasks')).toBeInTheDocument();
  });

  it('无任何页面权限时回退固定首页', () => {
    signInAs({ id: 'u_1', name: '空权限', roles: [], permissions: [] });
    renderLogin('/login');
    expect(screen.getByText('落地页=/tasks')).toBeInTheDocument();
  });
});
