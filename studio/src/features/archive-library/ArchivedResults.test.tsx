import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AccessProvider } from '@/shared/access/AccessProvider';
import { ApiError } from '@/shared/api/types';
import type { ReplacementRun } from '@/entities/template/model/replacement.types';
import { ArchivedResults } from './ArchivedResults';

interface CatalogState {
  data?: {runs: ReplacementRun[]};
  error: unknown;
  isPending: boolean;
  isFetching: boolean;
  refetch: () => void;
}

const mock = vi.hoisted(() => ({catalog: {} as CatalogState, options: [] as unknown[]}));

vi.mock('@/entities/template/api/replacement.queries', () => ({
  useReplacementCatalog: (options?: unknown) => {
    mock.options.push(options);
    return mock.catalog;
  },
}));

vi.mock('@/entities/asset/api/archive.queries', () => ({
  useArchivedMedia: () => ({data: new Blob(['x']), isPending: false, error: null, refetch: vi.fn()}),
  useArchiveDownload: () => ({mutate: vi.fn(), isPending: false, error: null}),
}));

function run(overrides: Partial<ReplacementRun> = {}): ReplacementRun {
  return {
    id: 'run1', kind: 'character', target: '人物A', status: 'success', archivePath: 'runs/2026/11/06/a',
    createdAt: '2026-11-06T02:00:00Z', prompt: '', reviewStatus: 'pending',
    outputUrl: '/api/v1/templates/replacement-runs/run1/output', ...overrides,
  };
}

function App({permissions = ['asset:read', 'template:read']}: {permissions?: string[]}) {
  return <MemoryRouter>
    <AccessProvider subject={{id: 'owner', name: '用户', roles: [], permissions}}>
      <ArchivedResults />
    </AccessProvider>
  </MemoryRouter>;
}

beforeEach(() => {
  mock.options = [];
  mock.catalog = {data: undefined, error: null, isPending: false, isFetching: false, refetch: vi.fn()};
});

describe('生成结果区', () => {
  it('没有模板读取权限时不请求目录，也不显示暂无结果', () => {
    render(<App permissions={['asset:read']} />);
    expect(mock.options).toContainEqual({enabled: false});
    expect(screen.getByText(/没有查看生成结果的权限/)).toBeVisible();
    expect(screen.queryByText(/暂无已归档的生成结果/)).toBeNull();
  });

  it('加载中不显示空态', () => {
    mock.catalog = {data: undefined, error: null, isPending: true, isFetching: true, refetch: vi.fn()};
    render(<App />);
    expect(screen.getByRole('status', {name: '正在读取生成结果'})).toBeVisible();
    expect(screen.queryByText(/暂无已归档的生成结果/)).toBeNull();
  });

  it('读取失败给出重试，不伪装成没有结果', () => {
    mock.catalog = {data: undefined, error: new ApiError('模板目录不可用', 500), isPending: false, isFetching: false, refetch: vi.fn()};
    render(<App />);
    expect(screen.getByText('模板目录不可用')).toBeVisible();
    expect(screen.getByRole('button', {name: '重试'})).toBeVisible();
    expect(screen.queryByText(/暂无已归档的生成结果/)).toBeNull();
  });

  it('成功且无成片时才显示空态', () => {
    mock.catalog = {data: {runs: [run({outputUrl: null})]}, error: null, isPending: false, isFetching: false, refetch: vi.fn()};
    render(<App />);
    expect(screen.getByText('暂无已归档的生成结果')).toBeVisible();
  });

  it('成片卡片区分生成结果与人工验收', () => {
    mock.catalog = {data: {runs: [run()]}, error: null, isPending: false, isFetching: false, refetch: vi.fn()};
    render(<App />);
    expect(screen.getByText('人物A')).toBeVisible();
    expect(screen.getByText('成片已保存')).toBeVisible();
    expect(screen.getByText('人工验收：待验收')).toBeVisible();
    expect(screen.getByText(/生成结果归档 · 1 份/)).toBeVisible();
  });
});
