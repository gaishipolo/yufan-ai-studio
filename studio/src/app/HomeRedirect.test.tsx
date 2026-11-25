import { Boxes } from 'lucide-react';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { AccessProvider } from '@/shared/access/AccessProvider';
import type { Subject } from '@/shared/access/types';
import { createRegistry, defineModule } from '@/shared/registry';
import { RegistryProvider } from '@/shared/registry/RegistryProvider';
import { HomeRedirect } from './HomeRedirect';

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

const subject = (permissions: string[]): Subject => ({
  id: 'u_test',
  name: '测试',
  roles: [],
  permissions,
});

function LandingProbe() {
  const location = useLocation();
  return <div>落地页={location.pathname}</div>;
}

function renderHome(who: Subject) {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <AccessProvider subject={who}>
        <RegistryProvider registry={registry}>
          <Routes>
            <Route path="/" element={<HomeRedirect />} />
            <Route path="*" element={<LandingProbe />} />
          </Routes>
        </RegistryProvider>
      </AccessProvider>
    </MemoryRouter>,
  );
}

describe('HomeRedirect · 根路由落地页', () => {
  it('点品牌名回根地址时，落到该用户能打开的第一页', () => {
    renderHome(subject(['page:image', 'page:task-center']));
    expect(screen.getByText('落地页=/image')).toBeInTheDocument();
  });

  it('与登录页同一判据：无权限时回退固定首页', () => {
    renderHome(subject([]));
    expect(screen.getByText('落地页=/tasks')).toBeInTheDocument();
  });
});
