import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { LibraryDetail } from './LibraryDetail';

const { detailMock, mediaMock, textMock, patchMock, saveFileMock, toastMock } = vi.hoisted(() => ({
  detailMock: vi.fn(),
  mediaMock: vi.fn(),
  textMock: vi.fn(),
  patchMock: vi.fn(),
  saveFileMock: vi.fn(),
  toastMock: vi.fn(),
}));

vi.mock('@/entities/library/api/library.queries', () => ({
  useLibraryDetail: detailMock,
  useLibraryMedia: mediaMock,
  useReadText: textMock,
  usePatchAsset: () => ({ mutate: patchMock, isPending: false }),
  useSaveAssetFile: () => ({ mutate: saveFileMock, isPending: false }),
}));

/* 弹窗内的重组件与本文件要验的两件事无关，替掉省掉一层查询 */
vi.mock('@/entities/library/ui/PromptRefs', () => ({
  PromptRefs: ({ value }: { value: string }) => <span>{value}</span>,
}));
vi.mock('./TextAssetModal', () => ({ TextAssetModal: () => null }));
vi.mock('./TextVersionHistory', () => ({ TextVersionHistory: () => null }));
vi.mock('./use-create-tag-inline', () => ({ useCreateTagInline: () => undefined }));
vi.mock('@/shared/lib/toast', () => ({ toast: toastMock }));

const IMAGE = {
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
  tags: [{ id: 't1', name: '产品' }],
};

const TEXT_ASSET = { ...IMAGE, id: 't1', name: '脚本.md', kind: 'text', tags: [] };

function renderDetail(asset: Record<string, unknown>) {
  detailMock.mockReturnValue({ data: asset, error: null, isPending: false, refetch: vi.fn() });
  return render(
    <LibraryDetail
      assetId="a1"
      tags={[]}
      viewer={{ id: 'u1', canDeleteAny: false }}
      canUpdate
      onClose={vi.fn()}
      onAction={vi.fn()}
    />,
  );
}

beforeEach(() => {
  detailMock.mockReset();
  mediaMock.mockReset();
  textMock.mockReset();
  patchMock.mockReset();
  toastMock.mockReset();
  mediaMock.mockReturnValue({ url: undefined, isPending: false, isError: false });
  textMock.mockReturnValue({ data: undefined, isPending: false, isError: false });
});

describe('LibraryDetail · 改名不被刷新冲掉', () => {
  it('详情重新拉取（tags 是新引用）后，正在编辑的名字与编辑态都还在', async () => {
    const user = userEvent.setup();
    const view = renderDetail(IMAGE);

    await user.click(screen.getByRole('button', { name: '重命名' }));
    const input = screen.getByRole('textbox', { name: '资产名称' }) as HTMLInputElement;
    await user.clear(input);
    await user.type(input, '新名字');
    expect(input.value).toBe('新名字');

    /* 窗口重新聚焦 / 写操作后的失效都会重新拉一次详情：
       响应里的 tags 是**新数组引用**，名字也还是服务端那个旧名。 */
    detailMock.mockReturnValue({
      data: { ...IMAGE, tags: [{ id: 't1', name: '产品' }] },
      error: null,
      isPending: false,
      refetch: vi.fn(),
    });
    view.rerender(
      <LibraryDetail
        assetId="a1"
        tags={[]}
        viewer={{ id: 'u1', canDeleteAny: false }}
        canUpdate
        onClose={vi.fn()}
        onAction={vi.fn()}
      />,
    );

    const after = screen.getByRole('textbox', { name: '资产名称' }) as HTMLInputElement;
    expect(after.value).toBe('新名字');
  });
});

describe('LibraryDetail · 复制全文', () => {
  it('正文还没读出来时按钮禁用，不复制空串却说「已复制全文」', () => {
    textMock.mockReturnValue({ data: undefined, isPending: true, isError: false });
    renderDetail(TEXT_ASSET);

    expect(screen.getByRole('button', { name: '复制全文' })).toBeDisabled();
  });

  it('正文读到了才可点', () => {
    textMock.mockReturnValue({
      data: { id: 't1', content: '雨夜街头', version: 3 },
      isPending: false,
      isError: false,
    });
    renderDetail(TEXT_ASSET);

    expect(screen.getByRole('button', { name: '复制全文' })).toBeEnabled();
  });
});
