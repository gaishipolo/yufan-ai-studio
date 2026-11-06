import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AccessProvider } from '@/shared/access/AccessProvider';
import { ApiError } from '@/shared/api/types';
import type { ArchivedAsset } from '@/entities/asset/model/archive.types';
import { ArchiveLibrary } from './ArchiveLibrary';
import { useUploadBatchStore } from './model/use-upload-batch';

interface ListState {
  data?: {root: string; items: ArchivedAsset[]; total: number; usedBytes: number};
  error: unknown;
  isPending: boolean;
  isFetching: boolean;
  refetch: () => void;
}

const mock = vi.hoisted(() => ({
  list: {} as ListState,
  mediaCalls: [] as (string | undefined)[],
  thumbBroken: false,
  streamBroken: false,
  upload: vi.fn(),
  download: vi.fn(),
}));

vi.mock('@/entities/asset/api/archive.queries', () => ({
  archiveKeys: {all: (owner: string) => ['archive-assets', owner]},
  useArchiveAssets: () => mock.list,
  useArchiveUpload: () => ({mutateAsync: mock.upload, isPending: false}),
  useArchiveImport: () => ({mutate: vi.fn(), isPending: false}),
  useArchiveDownload: () => ({mutate: mock.download, isPending: false, error: null}),
  useArchivedMedia: (url?: string) => {
    mock.mediaCalls.push(url);
    const broken = (mock.thumbBroken && !!url?.includes('/thumbnail'))
      || (mock.streamBroken && !!url?.includes('/stream'));
    return {data: broken ? undefined : new Blob(['x']), isPending: false, refetch: vi.fn(),
      error: broken ? new Error('预览读取失败') : null};
  },
}));

vi.mock('@/entities/template/api/replacement.queries', () => ({
  useReplacementCatalog: () => ({data: {runs: []}, isPending: false, isFetching: false, error: null, refetch: vi.fn()}),
}));

function asset(overrides: Partial<ArchivedAsset> = {}): ArchivedAsset {
  return {
    id: 'a1', name: '原片.mp4', category: 'source', type: 'video', mimeType: 'video/mp4',
    sizeBytes: 2048, path: 'library/source/2026/11/a1.mp4', sha256: 'sha', createdAt: '2026-11-06T02:00:00Z',
    duration: 8, hasAudio: true, width: 1080, height: 1920,
    url: '/api/v1/assets/archive/a1/file', previewUrl: '/api/v1/assets/archive/a1/thumbnail', ...overrides,
  };
}

function loaded(items: ArchivedAsset[], usedBytes = 2048): ListState {
  return {data: {root: 'Z:\\yufan_ai_studio', items, total: items.length, usedBytes},
    error: null, isPending: false, isFetching: false, refetch: vi.fn()};
}

function App({permissions = ['asset:read', 'asset:create']}: {permissions?: string[]}) {
  return <MemoryRouter>
    <AccessProvider subject={{id: 'owner', name: '用户', roles: [], permissions}}>
      <ArchiveLibrary />
    </AccessProvider>
  </MemoryRouter>;
}

beforeEach(() => {
  mock.mediaCalls = [];
  mock.thumbBroken = false;
  mock.streamBroken = false;
  mock.upload.mockReset();
  mock.download.mockReset();
  useUploadBatchStore.setState({batch: null});
  Object.defineProperty(URL, 'createObjectURL', {value: vi.fn(() => 'blob:mock'), configurable: true});
  Object.defineProperty(URL, 'revokeObjectURL', {value: vi.fn(), configurable: true});
});

describe('素材列表状态', () => {
  it('首次读取失败时不显示空态与零统计', () => {
    mock.list = {data: undefined, error: new ApiError('归档盘未连接', 500), isPending: false, isFetching: false, refetch: vi.fn()};
    render(<App />);
    expect(screen.getByText('归档盘未连接')).toBeVisible();
    expect(screen.getByRole('button', {name: '重试'})).toBeVisible();
    expect(screen.queryByText(/暂无原片素材/)).toBeNull();
    expect(screen.getByText(/当前分类/).textContent).toContain('—');
  });

  it('有缓存时刷新失败保留列表并标明是上次内容', () => {
    mock.list = {...loaded([asset()]), error: new ApiError('更新失败', 500), isFetching: false};
    render(<App />);
    expect(screen.getByText('原片.mp4')).toBeVisible();
    expect(screen.getByText(/更新失败，以下为上次加载的素材/)).toBeVisible();
  });

  it('更新失败且上次读到 0 条时，不声称下面有素材', () => {
    mock.list = {...loaded([]), error: new ApiError('更新失败', 500), isFetching: false};
    render(<App />);
    expect(screen.getByText(/上一次加载也没有读到任何素材/)).toBeVisible();
    expect(screen.queryByText(/以下为上次加载的素材/)).toBeNull();
  });

  it('只有请求成功且确实为空时才显示空态与 0', () => {
    mock.list = loaded([]);
    render(<App />);
    expect(screen.getByText('暂无原片素材')).toBeVisible();
    expect(screen.getByText(/当前分类/).textContent).toContain('0 项');
    expect(screen.getAllByRole('button', {name: '上传原片'}).length).toBeGreaterThan(0);
  });

  it('只读账号的空态不引导上传', () => {
    mock.list = loaded([]);
    render(<App permissions={['asset:read']} />);
    expect(screen.getByText(/请联系素材维护人员/)).toBeVisible();
    expect(screen.queryByRole('button', {name: /上传/})).toBeNull();
  });
});

describe('素材预览与下载', () => {
  it('视频详情用兼容流播放，下载仍取原件', () => {
    mock.list = loaded([asset()]);
    render(<App />);
    expect(screen.getByText('可用于替换')).toBeVisible();
    fireEvent.click(screen.getByRole('button', {name: /原片\.mp4/}));
    expect(mock.mediaCalls).toContain('/api/v1/assets/archive/a1/stream?v=hd-v2');
    fireEvent.click(screen.getByRole('button', {name: '下载原始文件'}));
    expect(mock.download).toHaveBeenCalledWith({url: '/api/v1/assets/archive/a1/file', filename: '原片.mp4'});
  });

  it('缩略图读取失败显示预览不可用，而不是永久加载', () => {
    mock.thumbBroken = true;
    mock.list = loaded([asset()]);
    render(<App />);
    expect(screen.getByText('预览不可用')).toBeVisible();
    expect(screen.queryByText('加载预览…')).toBeNull();
  });

  it('缺少音轨的原片标注具体原因，不影响归档展示', () => {
    mock.list = loaded([asset({hasAudio: false})]);
    render(<App />);
    expect(screen.getByText('缺少音轨')).toBeVisible();
    expect(screen.getByText('原片.mp4')).toBeVisible();
  });

  it('预览读不出来时下载入口仍然可用', () => {
    mock.streamBroken = true;
    mock.list = loaded([asset()]);
    render(<App />);
    fireEvent.click(screen.getByRole('button', {name: /原片\.mp4/}));
    expect(screen.getByText('预览读取失败')).toBeVisible();
    fireEvent.click(screen.getByRole('button', {name: '下载原始文件'}));
    expect(mock.download).toHaveBeenCalledWith({url: '/api/v1/assets/archive/a1/file', filename: '原片.mp4'});
  });
});

describe('批次期间的互斥', () => {
  it('批次在途时分类按钮与上传入口一起禁用', () => {
    mock.list = loaded([asset()]);
    useUploadBatchStore.setState({batch: {
      id: 'b1', category: 'source', startedAt: Date.now(), phase: 'running',
      items: [{key: 'k1', file: new File(['x'], 'a.mp4'), status: 'uploading'}],
    }});
    render(<App />);
    expect(screen.getByRole('button', {name: '人物'})).toBeDisabled();
    expect(screen.getByRole('button', {name: '上传原片'})).toBeDisabled();
    expect(screen.getByText(/本批次归档到：原片/)).toBeVisible();
  });
});
