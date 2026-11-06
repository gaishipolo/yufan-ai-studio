import { beforeEach, expect, it, vi } from 'vitest';
import { useSessionStore } from '@/shared/access/session';
import { useUploadBatchStore } from './use-upload-batch';

const mock = vi.hoisted(() => ({upload: vi.fn(), invalidate: vi.fn().mockResolvedValue(undefined)}));
vi.mock('@/entities/asset/api/archive.api', () => ({archiveApi: {upload: mock.upload}}));
vi.mock('@/entities/asset/api/archive.queries', () => ({archiveKeys: {all: (id: string) => ['archive-assets', id]}}));
vi.mock('@/shared/api/query-client', () => ({queryClient: {invalidateQueries: mock.invalidate}}));

function subject(id: string) {
  useSessionStore.setState({subject: {id, name: id, roles: [], permissions: ['asset:create']}});
}
beforeEach(() => {mock.upload.mockReset(); mock.invalidate.mockClear(); useUploadBatchStore.setState({batch: null, ownerId: null}); subject('A');});

it('账号切换清除旧批次，旧请求返回不再上传下一条', async () => {
  let finish!: (value: {id: string}) => void;
  mock.upload.mockReturnValueOnce(new Promise(resolve => {finish = resolve;}));
  useUploadBatchStore.getState().start([new File(['a'], 'A-private.mp4'), new File(['b'], 'A-second.mp4')], 'source');
  expect(mock.upload).toHaveBeenCalledTimes(1);
  subject('B');
  expect(useUploadBatchStore.getState().batch).toBeNull();
  finish({id: 'saved-for-A'});
  await vi.waitFor(() => expect(mock.invalidate).toHaveBeenCalled());
  expect(mock.upload).toHaveBeenCalledTimes(1);
  expect(mock.invalidate).toHaveBeenCalledWith({queryKey: ['archive-assets', 'A']});
});

it('新账号在旧请求返回前开始的新批次不会被旧回执覆盖', async () => {
  let finish!: (value: {id: string}) => void;
  mock.upload.mockReturnValueOnce(new Promise(resolve => {finish = resolve;})).mockResolvedValue({id: 'B-file'});
  useUploadBatchStore.getState().start([new File(['a'], 'A.mp4')], 'source');
  subject('B');
  useUploadBatchStore.getState().start([new File(['b'], 'B.mp4')], 'source');
  finish({id: 'A-file'});
  await vi.waitFor(() => expect(useUploadBatchStore.getState().batch?.phase).toBe('done'));
  expect(mock.upload).toHaveBeenCalledTimes(2);
  expect(useUploadBatchStore.getState().ownerId).toBe('B');
  expect(useUploadBatchStore.getState().batch?.items[0].assetId).toBe('B-file');
});
