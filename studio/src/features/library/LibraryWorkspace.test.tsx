import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { LibraryAssetOut } from '@/shared/api/schema';
import { LibraryWorkspace } from './LibraryWorkspace';

const { listMock, bulkMock, toastMock } = vi.hoisted(() => ({
  listMock: vi.fn(),
  bulkMock: vi.fn(),
  toastMock: vi.fn(),
}));

vi.mock('@/entities/library/api/library.queries', () => ({
  useLibraryList: listMock,
  useLibraryTags: () => ({ data: [], isPending: false }),
  useBulkAction: () => ({ mutate: bulkMock, isPending: false }),
  useCreateDownload: () => ({ mutate: vi.fn(), isPending: false }),
  useCreateTag: () => ({ mutateAsync: vi.fn() }),
  useDeleteAsset: () => ({ mutate: vi.fn(), isPending: false }),
  useDraftDecision: () => ({ mutate: vi.fn(), isPending: false }),
  useMyGroups: () => ({ data: [], isPending: false }),
  usePatchAsset: () => ({ mutate: vi.fn(), isPending: false }),
  usePurgeAsset: () => ({ mutate: vi.fn(), isPending: false }),
  useRestoreAsset: () => ({ mutate: vi.fn(), isPending: false }),
  useSaveAssetFile: () => ({ mutate: vi.fn(), isPending: false }),
  useShareRequests: () => ({ data: [], isPending: false }),
}));

vi.mock('@/shared/access/use-access', () => ({
  useCan: () => true,
  useSubject: () => ({ id: 'u1' }),
}));
vi.mock('@/shared/lib/toast', () => ({ toast: toastMock }));

/* 弹窗型重组件与本文件要验的两件事无关，替掉省掉一层查询 */
vi.mock('./LibraryDetail', () => ({ LibraryDetail: () => null }));
vi.mock('./LibraryUploadModal', () => ({ LibraryUploadModal: () => null }));
vi.mock('./LibraryShareModal', () => ({ LibraryShareModal: () => null }));
vi.mock('./LibraryDownloadsModal', () => ({ LibraryDownloadsModal: () => null }));
vi.mock('./ShareRequestsModal', () => ({ ShareRequestsModal: () => null }));
vi.mock('./LibraryTagManager', () => ({ LibraryTagManager: () => null }));
vi.mock('./LibraryTagPicker', () => ({ LibraryTagPicker: () => null }));
vi.mock('./LibraryToolbar', () => ({ LibraryToolbar: () => null }));
vi.mock('./LibraryTagBar', () => ({ LibraryTagBar: () => null }));

const ASSET: LibraryAssetOut = {
  id: 'a1',
  name: '产品图.png',
  kind: 'image',
  origin: 'upload',
  ownerId: 'u1',
  ownerName: '老大',
  visibility: 'personal',
  state: 'active',
  sizeBytes: 1024,
  createdAt: '2026-11-10T02:00:00Z',
  tags: [],
};

const OTHER: LibraryAssetOut = { ...ASSET, id: 'a2', name: '另一张.png' };

function listOf(items: LibraryAssetOut[]) {
  return {
    data: { items, total: items.length },
    isPending: false,
    error: null,
    refetch: vi.fn(),
  };
}

function renderWorkspace() {
  return render(
    <MemoryRouter initialEntries={['/library?view=all']}>
      <LibraryWorkspace />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  listMock.mockReset();
  bulkMock.mockReset();
  toastMock.mockReset();
  listMock.mockReturnValue(listOf([ASSET]));
});

describe('LibraryWorkspace · 批量删除要确认', () => {
  it('点「移入回收站」先弹确认，确认后才发批量请求', async () => {
    const user = userEvent.setup();
    renderWorkspace();

    await user.click(screen.getByRole('button', { name: '全选本页' }));
    expect(screen.getByText('已选 1 项')).toBeInTheDocument();

    /* 卡片上也有一个「移入回收站」：批量条里的那个排在后面 */
    await user.click(screen.getAllByRole('button', { name: '移入回收站' }).at(-1)!);

    // 还没确认之前一个请求都不该发出去
    expect(bulkMock).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: '批量移入回收站' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '批量移入回收站' }));

    expect(bulkMock).toHaveBeenCalledWith(
      { ids: ['a1'], action: 'delete', payload: { reason: '' } },
      expect.anything(),
    );
  });

  it('共享出去的素材不能直接批量删：后端必然拒，界面先说清楚', async () => {
    const user = userEvent.setup();
    listMock.mockReturnValue(listOf([{ ...ASSET, visibility: 'group' }]));
    renderWorkspace();

    await user.click(screen.getByRole('button', { name: '全选本页' }));
    await user.click(screen.getAllByRole('button', { name: '移入回收站' }).at(-1)!);

    expect(bulkMock).not.toHaveBeenCalled();
    expect(toastMock).toHaveBeenCalledWith(
      '选中的素材共享给小组或团队，不能直接移入回收站',
      'error',
    );
  });
});

describe('LibraryWorkspace · 选中项随列表裁剪', () => {
  it('列表数据变了（换筛选 / 翻页 / 删掉一条）之后，看不见的选中项不再留在批量里', async () => {
    const user = userEvent.setup();
    const view = renderWorkspace();

    await user.click(screen.getByRole('button', { name: '全选本页' }));
    expect(screen.getByText('已选 1 项')).toBeInTheDocument();

    /* 列表换成另一批数据（筛选条件变了 / 翻页了 / 那条被删了） */
    listMock.mockReturnValue(listOf([OTHER]));
    view.rerender(
      <MemoryRouter initialEntries={['/library?view=all']}>
        <LibraryWorkspace />
      </MemoryRouter>,
    );

    expect(screen.queryByText('已选 1 项')).not.toBeInTheDocument();
  });
});

describe('LibraryWorkspace · URL 里的类型筛选', () => {
  /* 白名单与 `KIND_OPTIONS` 手抄过三次，每次的表现都一样：`?kind=…` 写进 URL
     又被判回 `all`，用户看到「点了筛选没反应」。现在的判据是「白名单从筛选
     选项派生」—— 新加一档忘了同步这件事不该再靠人记。 */
  it('?kind=pack 能落到查询条件上，不被判回「全部」', () => {
    render(
      <MemoryRouter initialEntries={['/library?view=all&kind=pack']}>
        <LibraryWorkspace />
      </MemoryRouter>,
    );
    expect(listMock).toHaveBeenCalledWith(expect.objectContaining({ kind: 'pack' }));
  });

  it('不认识的 kind 仍退回「全部」（白名单没被放开成任意字符串）', () => {
    render(
      <MemoryRouter initialEntries={['/library?view=all&kind=不存在']}>
        <LibraryWorkspace />
      </MemoryRouter>,
    );
    expect(listMock).toHaveBeenCalledWith(expect.objectContaining({ kind: undefined }));
  });
});
