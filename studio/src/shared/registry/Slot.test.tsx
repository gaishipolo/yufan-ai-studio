import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { AccessProvider } from '@/shared/access/AccessProvider';
import type { Subject } from '@/shared/access/types';
import { createRegistry } from './create-registry';
import { defineModule } from './define-module';
import { RegistryProvider } from './RegistryProvider';
import { Slot } from './Slot';

const load = () => Promise.resolve({ default: () => null });

const registry = createRegistry([
  defineModule({
    id: 'demo',
    routes: [{ path: 'demo', load }],
    slots: [
      {
        name: 'topbar.actions',
        order: 2,
        component: () => <span>公开按钮</span>,
      },
      {
        name: 'topbar.actions',
        order: 1,
        permissions: ['user:manage'],
        component: () => <span>管理按钮</span>,
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

describe('Slot', () => {
  it('按 order 渲染所有可见贡献', () => {
    render(
      <AccessProvider subject={subject(['*'])}>
        <RegistryProvider registry={registry}>
          <Slot name="topbar.actions" />
        </RegistryProvider>
      </AccessProvider>,
    );

    const visible = screen.getAllByText(/按钮/).map((node) => node.textContent);
    expect(visible).toEqual(['管理按钮', '公开按钮']);
  });

  it('无权限的贡献不渲染', () => {
    render(
      <AccessProvider subject={subject([])}>
        <RegistryProvider registry={registry}>
          <Slot name="topbar.actions" />
        </RegistryProvider>
      </AccessProvider>,
    );

    expect(screen.queryByText('管理按钮')).toBeNull();
    expect(screen.getByText('公开按钮')).toBeTruthy();
  });

  it('没有 Provider / 没有贡献时渲染 fallback', () => {
    render(<Slot name="sidebar.footer" fallback={<span>占位</span>} />);
    expect(screen.getByText('占位')).toBeTruthy();
  });
});
