import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { LibraryAssetOut } from '@/shared/api/schema';
import { LibraryWorkspace } from './LibraryWorkspace';

const { listMock, bulkMock, toastMock, createDownloadMock, detailMock } = vi.hoisted(() => ({
  listMock: vi.fn(),
  bulkMock: vi.fn(),
  toastMock: vi.fn(),
  createDownloadMock: vi.fn(),
  detailMock: vi.fn(),
}));

/* 「编辑封装」要先拉一次详情（卡片上只有 memberCount，编辑器要成员行）。 */
vi.mock('@/entities/library/api/library.api', () => ({
  libraryApi: { detail: detailMock },
}));

vi.mock('@/entities/library/api/library.queries', () => ({
  useLibraryList: listMock,
  useLibraryTags: () => ({ data: [], isPending: false }),
  useBulkAction: () => ({ mutate: bulkMock, isPending: false }),
  useCreateDownload: () => ({ mutate: createDownloadMock, isPending: false }),
  useCreateTag: () => ({ mutateAsync: vi.fn() }),
  useDeleteAsset: () => ({
    mutate: (_vars: unknown, options?: {onSuccess?: () => void}) => options?.onSuccess?.(),
    isPending: false,
  }),
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
/* 建封装弹窗内部有选择器与 mutation，本文件不关心；它自己的行为在
   PackCreateModal.test.tsx 里验。 */
vi.mock('./PackCreateModal', () => ({
  PackCreateModal: ({ open, pack }: { open: boolean; pack?: { id: string } }) =>
    open && pack ? <div>正在编辑 {pack.id}</div> : null,
}));
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
  createDownloadMock.mockReset();
  detailMock.mockReset();
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

/** 封装：没有自己的字节，删它只解除归属。 */
const PACK: LibraryAssetOut = {
  ...ASSET,
  id: 'p1',
  name: '6036 黑',
  kind: 'pack',
  sizeBytes: 0,
  memberCount: 3,
};

describe('LibraryWorkspace · 删封装', () => {
  /* 删包只是解除归属：成员仍是自己的资产行，会回到个人库。
     不说的话，用户点完会看到资产库突然多出几张图 —— 界面上没有任何地方
     提过这件事，读起来像「回收站把包拆开了」。反过来，以为图跟着没了的人
     会去回收站里找，也找不到。 */
  it('移入回收站前说明：素材不会被删，会回到资产库', async () => {
    const user = userEvent.setup();
    listMock.mockReturnValue(listOf([PACK]));
    renderWorkspace();

    await user.click(screen.getByRole('button', { name: '移入回收站' }));

    expect(screen.getByText(/3 张素材不会被删除/)).toBeInTheDocument();
    expect(screen.getByText(/回到资产库单独显示/)).toBeInTheDocument();
  });

  it('删完的提示也带上成员去哪了', async () => {
    const user = userEvent.setup();
    listMock.mockReturnValue(listOf([PACK]));
    renderWorkspace();

    // 卡片上的删除 → 弹窗里确认
    await user.click(screen.getByRole('button', { name: '移入回收站' }));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', { name: '移入回收站' }));

    expect(toastMock).toHaveBeenCalledWith(
      expect.stringContaining('3 张素材已回到资产库'),
    );
  });

  it('普通素材的删除文案不带这句（免得每张图都啰嗦一遍）', async () => {
    const user = userEvent.setup();
    renderWorkspace();

    await user.click(screen.getByRole('button', { name: '移入回收站' }));

    expect(screen.queryByText(/回到资产库单独显示/)).not.toBeInTheDocument();
  });
});

/**
 * 封装卡片上的两个动作：打包下载 / 编辑封装。
 *
 * 两者都不能走素材那条链路：包没有自己的字节（单文件下载会得到「此资产没有
 * 原文件」），卡片上也拿不到成员行（编辑器要的是成员，不是 `memberCount`）。
 */
describe('LibraryWorkspace · 封装的两个动作', () => {
  const PACK: LibraryAssetOut = {
    id: 'p1',
    name: '6036 黑',
    kind: 'pack',
    origin: 'upload',
    ownerId: 'u1',
    visibility: 'personal',
    state: 'active',
    sizeBytes: 0,
    memberCount: 2,
    createdAt: '2026-11-19T02:00:00Z',
    tags: [],
  };

  beforeEach(() => {
    listMock.mockReturnValue(listOf([PACK]));
  });

  it('打包下载：建的是打包任务，不是单文件下载', async () => {
    const user = userEvent.setup();
    renderWorkspace();

    await user.click(await screen.findByRole('button', { name: '打包下载（成员共 2 张）' }));

    expect(createDownloadMock).toHaveBeenCalledWith(
      { ids: ['p1'] },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    );
  });

  it('编辑封装：先拉详情，再拿成员开弹窗', async () => {
    const user = userEvent.setup();
    detailMock.mockResolvedValue({
      id: 'p1',
      name: '6036 黑',
      packItems: [{ id: 'm1', name: '正面.png', kind: 'image' }],
    });
    renderWorkspace();

    await user.click(await screen.findByRole('button', { name: '编辑封装' }));
    expect(detailMock).toHaveBeenCalledWith('p1');

    // 弹窗拿到的是这个包（不是又开了一个「新建封装」）
    expect(await screen.findByText('正在编辑 p1')).toBeInTheDocument();
  });
});
