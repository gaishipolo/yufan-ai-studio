import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '@/shared/api/types';
import type { ArchivedAsset } from '@/entities/asset/model/archive.types';
import { ArchiveUploadPanel } from './ArchiveUploadPanel';
import { useUploadBatchStore } from './model/use-upload-batch';

const mock = vi.hoisted(() => ({
  upload: vi.fn(),
  assets: {data: {items: [] as ArchivedAsset[]}},
}));

vi.mock('@/entities/asset/api/archive.api', () => ({archiveApi: {upload: mock.upload}}));
vi.mock('@/entities/asset/api/archive.queries', () => ({
  /* store 里的统一刷新要用 key 工厂，mock 掉整个模块时必须一并提供。 */
  archiveKeys: {all: (owner: string) => ['archive-assets', owner]},
  useArchiveAssets: () => ({...mock.assets, refetch: vi.fn().mockResolvedValue(undefined)}),
}));

function file(name: string): File {
  return new File(['x'], name);
}

function archived(name: string): ArchivedAsset {
  return {
    id: 'found', name, category: 'source' as const, type: 'video' as const, mimeType: 'video/mp4',
    sizeBytes: 1, path: `library/source/2026/11/${name}`, sha256: 's',
    createdAt: new Date().toISOString(), url: '/api/v1/assets/archive/found/file',
  };
}

function start(names: string[]) {
  return act(async () => {
    useUploadBatchStore.getState().start(names.map(file), 'source');
  });
}

beforeEach(() => {
  mock.upload.mockReset();
  mock.assets = {data: {items: []}};
  useUploadBatchStore.setState({batch: null});
});

describe('上传批次面板', () => {
  it('第二个文件失败时逐条记录，后续文件标为未处理', async () => {
    mock.upload.mockResolvedValueOnce({id: 'a1'}).mockRejectedValueOnce(new ApiError('文件不能超过 300MB', 400));
    render(<ArchiveUploadPanel />);
    await start(['a.mp4', 'b.mp4', 'c.mp4']);
    await waitFor(() => expect(screen.getByText('归档失败 · 可重试')).toBeVisible());
    expect(screen.getByText('已归档')).toBeVisible();
    expect(screen.getByText('未处理')).toBeVisible();
    expect(screen.getByText(/成功 1 · 失败 1/)).toBeVisible();
    /* 暂停时必须有出口：否则分类被锁、面板关不掉，只能刷新页面。 */
    expect(screen.getByRole('button', {name: '关闭批次'})).toBeVisible();
  });

  it('明确失败提供重试，重试只重发这一条', async () => {
    mock.upload.mockRejectedValueOnce(new ApiError('类型不支持', 400)).mockResolvedValue({id: 'a2'});
    render(<ArchiveUploadPanel />);
    await start(['a.mp4', 'b.mp4']);
    await waitFor(() => expect(screen.getByRole('button', {name: '重试此项'})).toBeVisible());
    expect(mock.upload).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('button', {name: '重试此项'}));
    await waitFor(() => expect(mock.upload).toHaveBeenCalledTimes(3));
    await waitFor(() => expect(screen.queryByText('归档失败 · 可重试')).toBeNull());
  });

  it('超时归为结果待核对，不给重传入口', async () => {
    mock.upload.mockRejectedValueOnce(new ApiError('请求超时，请稍后重试', 408));
    render(<ArchiveUploadPanel />);
    await start(['a.mp4']);
    await waitFor(() => expect(screen.getByText('结果待核对 · 禁止直接重传')).toBeVisible());
    expect(screen.queryByRole('button', {name: '重试此项'})).toBeNull();
    expect(screen.getByRole('button', {name: '刷新并核对'})).toBeVisible();
  });

  it('核对时找到同名同大小的记录，由用户确认而不是自动判定', async () => {
    mock.assets = {data: {items: [archived('a.mp4')]}};
    mock.upload.mockRejectedValueOnce(new ApiError('请求超时，请稍后重试', 408));
    render(<ArchiveUploadPanel />);
    await start(['a.mp4']);
    await waitFor(() => expect(screen.getByRole('button', {name: '刷新并核对'})).toBeVisible());
    fireEvent.click(screen.getByRole('button', {name: '刷新并核对'}));
    await waitFor(() => expect(screen.getByRole('button', {name: '确认是这条'})).toBeVisible());
    fireEvent.click(screen.getByRole('button', {name: '确认是这条'}));
    await waitFor(() => expect(screen.getByText('已归档')).toBeVisible());
    expect(mock.upload).toHaveBeenCalledTimes(1);
  });

  it('核对没找到记录时只说找不到，不断言没保存', async () => {
    mock.upload.mockRejectedValueOnce(new ApiError('请求超时，请稍后重试', 408));
    render(<ArchiveUploadPanel />);
    await start(['a.mp4']);
    await waitFor(() => expect(screen.getByRole('button', {name: '刷新并核对'})).toBeVisible());
    fireEvent.click(screen.getByRole('button', {name: '刷新并核对'}));
    await waitFor(() => expect(screen.getByText(/没找到匹配记录/)).toBeVisible());
    expect(screen.queryByText(/没有保存/)).toBeNull();
  });

  it('跳过失败项后继续处理剩余项', async () => {
    mock.upload.mockRejectedValueOnce(new ApiError('类型不支持', 400)).mockResolvedValue({id: 'a2'});
    render(<ArchiveUploadPanel />);
    await start(['a.mp4', 'b.mp4']);
    await waitFor(() => expect(screen.getByRole('button', {name: '跳过'})).toBeVisible());
    fireEvent.click(screen.getByRole('button', {name: '跳过'}));
    await waitFor(() => expect(mock.upload).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByText('已跳过')).toBeVisible());
  });
});
