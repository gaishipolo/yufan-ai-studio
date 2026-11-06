import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, useNavigate } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { Boxes } from 'lucide-react';
import { AccessProvider } from '@/shared/access/AccessProvider';
import type { Subject } from '@/shared/access/types';
import { RegistryProvider } from '@/shared/registry/RegistryProvider';
import { createRegistry } from '@/shared/registry/create-registry';
import { defineModule } from '@/shared/registry/define-module';
import { Sidebar } from './Sidebar';

// 角标组件会真的取数，单测里替换掉，只验证「角标被渲染」这件事
vi.mock('@/entities/task/api/task.queries', () => ({
  useTaskStats: () => ({ data: { running: 3 } }),
}));

const load = () => Promise.resolve({ default: () => null });

const registry = createRegistry([
  defineModule({
    id: 'public',
    routes: [
      { path: 'public', load, nav: { label: '公开页', icon: Boxes, group: '通用' } },
    ],
  }),
  defineModule({
    id: 'admin',
    routes: [
      {
        path: 'admin',
        load,
        // 权限只写在 route 上：route.nav 派生出来的导航项自动继承
        permissions: ['user:manage'],
        nav: {
          label: '管理页',
          icon: Boxes,
          group: '通用',
          order: 2,
          badge: () => <span>3</span>,
        },
      },
    ],
  }),
  defineModule({
    id: 'ops',
    routes: [
      {
        path: 'ops',
        load,
        permissions: ['ops:read'],
        nav: { label: '运维', icon: Boxes, group: '运维' },
      },
    ],
  }),
]);

const subject = (permissions: string[]): Subject => ({
  id: 't',
  name: '测试',
  roles: [],
  permissions,
});

/** 测试用跳转按钮：验证「导航进分组时自动展开」 */
function GoTo({ to, label }: { to: string; label: string }) {
  const navigate = useNavigate();
  return (
    <button type="button" onClick={() => navigate(to)}>
      {label}
    </button>
  );
}

function renderSidebar(permissions: string[], initialPath = '/public') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AccessProvider subject={subject(permissions)}>
        <RegistryProvider registry={registry}>
          <MemoryRouter initialEntries={[initialPath]}>
            <GoTo to="/admin" label="去管理页" />
            <Sidebar />
          </MemoryRouter>
        </RegistryProvider>
      </AccessProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
});

describe('Sidebar', () => {
  it('从注册表渲染分组与导航，无权限项自动隐藏', () => {
    renderSidebar([]);

    expect(screen.getByText('通用')).toBeTruthy();
    expect(screen.getByRole('link', { name: /公开页/ })).toBeTruthy();
    expect(screen.queryByRole('link', { name: /管理页/ })).toBeNull();
  });

  it('有权限时渲染导航项与模块提供的角标', () => {
    renderSidebar(['user:manage']);

    expect(screen.getByRole('link', { name: /管理页/ })).toBeTruthy();
    expect(screen.getByText('3')).toBeTruthy();
  });

  it('权限过滤后没有可见项的分组不渲染标题', () => {
    renderSidebar([]);

    expect(screen.queryByText('运维')).toBeNull();
    expect(screen.queryByRole('link', { name: /运维/ })).toBeNull();
  });

  it('一级类目默认展开', () => {
    renderSidebar(['user:manage']);

    const header = screen.getByRole('button', { name: '通用' });
    expect(header.getAttribute('aria-expanded')).toBe('true');
    expect(header.getAttribute('aria-controls')).toBeTruthy();
    expect(screen.getByRole('link', { name: /公开页/ })).toBeTruthy();
  });

  it('点击标题可收起 / 展开，并把状态写入 localStorage', async () => {
    const user = userEvent.setup();
    renderSidebar(['user:manage']);

    const header = screen.getByRole('button', { name: '通用' });

    await user.click(header);
    expect(header.getAttribute('aria-expanded')).toBe('false');
    expect(window.localStorage.getItem('sf_sidebar.collapsedGroups')).toContain('通用');

    await user.click(header);
    expect(header.getAttribute('aria-expanded')).toBe('true');
    // 全部展开时不留空 key，避免 localStorage 里堆无用状态
    expect(window.localStorage.getItem('sf_sidebar.collapsedGroups')).toBeNull();
  });

  it('重挂载后沿用已保存的折叠状态', async () => {
    const user = userEvent.setup();
    const first = renderSidebar(['user:manage']);
    await user.click(screen.getByRole('button', { name: '通用' }));
    first.unmount();

    renderSidebar(['user:manage']);
    expect(screen.getByRole('button', { name: '通用' }).getAttribute('aria-expanded')).toBe('false');
  });

  it('进入折叠分组内的页面时自动展开', async () => {
    const user = userEvent.setup();
    renderSidebar(['user:manage']);

    const header = screen.getByRole('button', { name: '通用' });
    await user.click(header);
    expect(header.getAttribute('aria-expanded')).toBe('false');

    // /admin 属于「通用」分组：路由变化应把它展开，否则用户看不到当前高亮项
    await user.click(screen.getByRole('button', { name: '去管理页' }));
    expect(screen.getByRole('button', { name: '通用' }).getAttribute('aria-expanded')).toBe('true');
  });
});
