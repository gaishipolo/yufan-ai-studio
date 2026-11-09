import { beforeEach, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { AccessProvider } from '@/shared/access/AccessProvider';
import { SourceLibrary } from './SourceLibrary';

const mock = vi.hoisted(() => ({mutate: vi.fn(), refetch: vi.fn(),
  editable: true, shared: false, error: null as Error | null, hidden: false,
}));
vi.mock('@/entities/asset/api/archive.queries', () => ({
  useArchiveAssets: () => ({isPending: false, isFetching: false, error: mock.error, refetch: mock.refetch,
    data: mock.hidden ? undefined : {total: 1, items: [{id: 'source1', name: '我的原片.mp4', category: 'source', type: 'video',
      sizeBytes: 100, duration: 2, hasAudio: true, url: '/file', createdAt: '2026-11-06T00:00:00Z',
      stateVersion: 3, editable: mock.editable, shared: mock.shared, downloadAllowed: mock.editable}]} }),
  useArchiveChange: () => ({mutate: mock.mutate, isPending: false, isSuccess: false, error: null}),
  useArchivedMedia: () => ({data: undefined, error: null}),
  useArchiveDownload: () => ({isPending: false, mutate: vi.fn()}),
}));
vi.mock('./model/use-upload-batch', () => ({useUploadBatchStore: (selector: (state: unknown) => unknown) => selector({batch: null, start: vi.fn()})}));

/* 详情浮层的标签/血缘是两条独立请求，与"谁能看到哪些操作按钮"无关 */
vi.mock('@/entities/asset/api/tag.queries', () => ({
  useAssetTags: () => ({data: {assetId: 'source1', items: []}, isPending: false, error: null}),
  useSetAssetTags: () => ({mutate: vi.fn(), isPending: false}),
  useTagCatalog: () => ({data: {groups: []}, isPending: false, error: null}),
}));
vi.mock('@/entities/asset/api/lineage.queries', () => ({
  useAssetLineage: () => ({data: undefined, isPending: false, error: null}),
  useDeriveFromAsset: () => ({mutateAsync: vi.fn(), isPending: false}),
}));

function app(scope: 'mine' | 'public', permissions: string[]) {
  return <MemoryRouter><AccessProvider subject={{id: 'A', name: '测试', roles: [], permissions}}><SourceLibrary scope={scope} /></AccessProvider></MemoryRouter>;
}
beforeEach(() => {mock.mutate.mockReset(); mock.editable = true; mock.shared = false; mock.error = null; mock.hidden = false;});

it('本人分享只提交选中素材和期望版本，不改变其他记录', async () => {
  render(app('mine', ['asset:read', 'asset:create', 'asset:share']));
  await userEvent.click(screen.getByRole('button', {name: '分享至公共素材'}));
  expect(mock.mutate).toHaveBeenCalledWith({id: 'source1', action: 'sharing', expectedVersion: 3, shared: true});
  expect(screen.queryByRole('button', {name: '重命名'})).not.toBeInTheDocument();
});
it('公共原片不提供他人的管理和下载操作', async () => {
  mock.editable = false; mock.shared = true;
  render(app('public', ['asset:read', 'asset:update', 'asset:share', 'asset:delete']));
  expect(screen.queryByRole('button', {name: '取消分享'})).not.toBeInTheDocument();
  expect(screen.queryByRole('button', {name: '移入回收站'})).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', {name: /我的原片.mp4/}));
  expect(screen.queryByRole('button', {name: '下载原始文件'})).not.toBeInTheDocument();
});
it('首次读取失败保留错误而不伪装为无素材', () => {
  mock.error = new Error('归档盘不可用'); mock.hidden = true;
  render(app('mine', ['asset:read']));
  expect(screen.getByText('素材读取失败')).toBeInTheDocument();
  expect(screen.queryByText('暂无素材')).not.toBeInTheDocument();
});
