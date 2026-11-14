import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { ManifestWorkspace } from './ManifestWorkspace';

/* 只验「按钮按权限出现/消失」这一件事：数据与素材选择器都不是被测对象，
   挡掉它们才能在没有 QueryClientProvider 的情况下挂载。 */
const access = vi.hoisted(() => ({permissions: [] as string[]}));

vi.mock('@/shared/access', () => ({
  useCan: (permission: string) =>
    access.permissions.includes(permission) || access.permissions.includes('*'),
}));

vi.mock('@/entities/template/api/pack.queries', () => ({
  useAvailablePacks: () => ({data: {items: []}}),
}));

vi.mock('@/entities/template-manifest/api/manifest.queries', () => ({
  useManifests: () => ({
    isLoading: false,
    data: {
      items: [
        {
          id: 'm1',
          key: 'tpl-1',
          name: '演示封装',
          status: 'draft',
          version: 0,
          functions: [],
        },
      ],
    },
  }),
  useManifestMutations: () => ({
    validate: {isPending: false, mutateAsync: vi.fn()},
    save: {isPending: false, mutateAsync: vi.fn()},
    publish: {isPending: false, mutateAsync: vi.fn()},
    disable: {isPending: false, mutateAsync: vi.fn()},
  }),
}));

vi.mock('./AssetPicker', () => ({AssetPicker: () => null}));

const renderPage = () =>
  render(
    <MemoryRouter>
      <ManifestWorkspace />
    </MemoryRouter>,
  );

describe('模板封装页的权限门禁', () => {
  it('只读用户看不到新建、发布、停用', () => {
    access.permissions = ['template_pack:read'];
    renderPage();
    expect(screen.queryByText('新建封装')).toBeNull();
    expect(screen.queryByText('发布')).toBeNull();
    expect(screen.queryByText('停用')).toBeNull();
    // 列表本身仍然可看：只读不等于不可见。
    expect(screen.getByText('演示封装')).toBeTruthy();
  });

  it('有写权限但无发布权限时，只出现新建', () => {
    access.permissions = ['template_pack:read', 'template_pack:create'];
    renderPage();
    expect(screen.getByText('新建封装')).toBeTruthy();
    expect(screen.queryByText('发布')).toBeNull();
  });

  it('发布权限独立于写权限', () => {
    access.permissions = ['template_pack:read', 'template_pack:publish'];
    renderPage();
    expect(screen.queryByText('新建封装')).toBeNull();
    expect(screen.getByText('发布')).toBeTruthy();
  });
});
