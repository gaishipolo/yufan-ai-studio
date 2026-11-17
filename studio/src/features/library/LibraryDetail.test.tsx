import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { LibraryDetail } from './LibraryDetail';

const { detailMock, mediaMock, largeMock, textMock, patchMock, saveFileMock, toastMock } =
  vi.hoisted(() => ({
    detailMock: vi.fn(),
    mediaMock: vi.fn(),
    largeMock: vi.fn(),
    textMock: vi.fn(),
    patchMock: vi.fn(),
    saveFileMock: vi.fn(),
    toastMock: vi.fn(),
  }));

vi.mock('@/entities/library/api/library.queries', () => ({
  useLibraryDetail: detailMock,
  useLibraryMedia: mediaMock,
  useLibraryLargePreview: largeMock,
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
  coverUrl: 'https://cdn.test/cover.jpg',
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
  largeMock.mockReset();
  textMock.mockReset();
  patchMock.mockReset();
  toastMock.mockReset();
  mediaMock.mockReturnValue({ url: undefined, isPending: false, isError: false });
  largeMock.mockReturnValue({ url: undefined, isPending: false, isError: false });
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

describe('LibraryDetail · 图片显示大图', () => {
  it('有大图时显示大图，不显示封面', () => {
    largeMock.mockReturnValue({
      url: 'blob:large',
      isPending: false,
      isError: false,
    });
    renderDetail(IMAGE);

    const img = screen.getByAltText('产品图') as HTMLImageElement;
    expect(img.src).toContain('blob:large');
    /* 封面是 360×240 的糊块：详情页要给人看的必须是那一张 */
    expect(document.querySelector('img[src*="cover.jpg"]')).toBeNull();
  });

  it('大图还在路上时先给封面，页面不空着', () => {
    /* 大图是惰性派生的，第一次要现生成（实测 0.4 秒上下）。
       这段时间里给封面，用户看到的是一张小图而不是一片空白。 */
    largeMock.mockReturnValue({ url: undefined, isPending: true, isError: false });
    renderDetail(IMAGE);

    const img = screen.getByAltText('产品图') as HTMLImageElement;
    expect(img.src).toContain('cover.jpg');
  });

  it('大图取不到时退回封面，不是一张破图', () => {
    largeMock.mockReturnValue({ url: undefined, isPending: false, isError: true });
    renderDetail(IMAGE);

    const img = screen.getByAltText('产品图') as HTMLImageElement;
    expect(img.src).toContain('cover.jpg');
  });

  it('大图与封面都取不到时退回原图', () => {
    largeMock.mockReturnValue({ url: undefined, isPending: false, isError: true });
    mediaMock.mockReturnValue({ url: 'blob:original', isPending: false, isError: false });
    renderDetail({ ...IMAGE, coverUrl: null });

    const img = screen.getByAltText('产品图') as HTMLImageElement;
    expect(img.src).toContain('blob:original');
  });

  it('换一条资产时大图跟着换，不会留着上一张', () => {
    largeMock.mockReturnValue({ url: 'blob:large-1', isPending: false, isError: false });
    const view = renderDetail(IMAGE);
    expect((screen.getByAltText('产品图') as HTMLImageElement).src).toContain('blob:large-1');

    largeMock.mockReturnValue({ url: 'blob:large-2', isPending: false, isError: false });
    detailMock.mockReturnValue({
      data: { ...IMAGE, id: 'a2', name: '另一张.png' },
      error: null,
      isPending: false,
      refetch: vi.fn(),
    });
    view.rerender(
      <LibraryDetail
        assetId="a2"
        tags={[]}
        viewer={{ id: 'u1', canDeleteAny: false }}
        canUpdate
        onClose={vi.fn()}
        onAction={vi.fn()}
      />,
    );

    const img = screen.getByAltText('另一张') as HTMLImageElement;
    expect(img.src).toContain('blob:large-2');
  });

  it('文本资产不显示图', () => {
    largeMock.mockReturnValue({ url: 'blob:large', isPending: false, isError: false });
    renderDetail(TEXT_ASSET);

    /* 文本资产走「正文」那条分支，媒体区里没有图。
       （不去要大图这件事由「文本不进 MediaImage」保证，不另写断言 ——
        那条断言测不出区别：文本在第一个三元分支就被拦住了，
        放宽第二个分支的判据也不会让它碰到大图请求。） */
    expect(document.querySelector('img')).toBeNull();
  });
});
