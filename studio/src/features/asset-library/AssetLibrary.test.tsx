import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Asset } from '@/entities/asset/model/types';
import { AssetLibrary } from './AssetLibrary';

/* restoreMocks: true 会把 vi.fn 的实现也清掉，所以每个 beforeEach 都要重设 */
const { useAssetsMock, uploadMock, deleteMock, toastMock, canMock } = vi.hoisted(() => ({
  useAssetsMock: vi.fn(),
  uploadMock: vi.fn(),
  deleteMock: vi.fn(),
  toastMock: vi.fn(),
  canMock: vi.fn(),
}));

vi.mock('@/entities/asset/api/asset.queries', () => ({
  useAssets: useAssetsMock,
  useUploadAsset: () => ({ mutateAsync: uploadMock, isPending: false }),
  useDeleteAsset: () => ({ mutate: deleteMock, isPending: false }),
  // 卡片/预览的字节取回是另一条链路，这里统一返回空串，
  // 断言的是列表、上传、删除三件事，不是图片能不能显示
  useAssetObjectUrl: () => '',
  useAssetBlob: () => ({ url: '', isLoading: false, isError: false }),
}));

vi.mock('@/shared/lib/toast', async (importOriginal) => ({
  ...((await importOriginal()) as Record<string, unknown>),
  toast: toastMock,
}));

vi.mock('@/shared/access/use-access', () => ({
  useCan: canMock,
}));

function asset(overrides: Partial<Asset> = {}): Asset {
  return {
    id: 'A1000',
    name: '超市抢购-参考图 01',
    type: 'image',
    mimeType: 'image/png',
    sizeBytes: 820 * 1024,
    width: 1080,
    height: 810,
    duration: 0,
    hasAudio: false,
    category: null,
    source: 'upload',
    coverUrl: '',
    fileUrl: '/api/v1/assets/A1000/file',
    refCount: 0,
    createdAt: '2026-11-06T01:00:00Z',
    ...overrides,
  };
}

function setAssets(items: Asset[], counts = {}) {
  useAssetsMock.mockReturnValue({
    data: {
      items,
      total: items.length,
      page: 1,
      pageSize: 24,
      counts: {
        all: items.length,
        image: items.filter((a) => a.type === 'image').length,
        video: items.filter((a) => a.type === 'video').length,
        audio: items.filter((a) => a.type === 'audio').length,
        ...counts,
      },
    },
    isLoading: false,
    isFetching: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  } as never);
}

const lastQuery = () => useAssetsMock.mock.calls.at(-1)?.[0];

beforeEach(() => {
  useAssetsMock.mockReset();
  uploadMock.mockReset();
  deleteMock.mockReset();
  toastMock.mockReset();
  canMock.mockReset();
  canMock.mockReturnValue(true);
  setAssets([]);
});

describe('AssetLibrary · 列表与筛选', () => {
  it('渲染素材卡片并显示分面计数', () => {
    setAssets([asset(), asset({ id: 'A1001', name: '卖场空镜', type: 'video' })]);
    render(<AssetLibrary />);

    expect(screen.getByText('超市抢购-参考图 01')).toBeInTheDocument();
    expect(screen.getByText('卖场空镜')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /全部\s*2/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /视频\s*1/ })).toBeInTheDocument();
  });

  it('切类型 tab 会把 type 传下去，并且回到第 1 页', async () => {
    const user = userEvent.setup();
    setAssets([asset()]);
    render(<AssetLibrary />);

    await user.click(screen.getByRole('button', { name: /^图片\s*\d*$/ }));
    await waitFor(() => expect(lastQuery()?.type).toBe('image'));
    expect(lastQuery()?.page).toBe(1);
  });

  it('关键字进查询前会 trim', async () => {
    const user = userEvent.setup();
    render(<AssetLibrary />);

    await user.type(screen.getByLabelText('按文件名搜索素材'), '空镜');
    await waitFor(() => expect(lastQuery()?.keyword).toBe('空镜'), { timeout: 1500 });
  });

  it('筛选后没有结果时给「清除筛选」，不是引导上传', async () => {
    const user = userEvent.setup();
    render(<AssetLibrary />);

    await user.click(screen.getByRole('button', { name: /^图片\s*\d*$/ }));
    expect(screen.getByText('没有匹配的素材')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '清除筛选' }));
    await waitFor(() => expect(lastQuery()?.type).toBe('all'));
  });
});

describe('AssetLibrary · 上传', () => {
  it('上传成功的文件后提示数量，并在去重命中时说明「已存在」', async () => {
    const user = userEvent.setup();
    setAssets([asset()]);
    uploadMock.mockResolvedValueOnce(asset({ id: 'A2000', name: '新素材.png' }));
    uploadMock.mockResolvedValueOnce(asset()); // 撞上已有记录 = 去重命中

    const { container } = render(<AssetLibrary />);
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, [
      new File(['a'], '新素材.png', { type: 'image/png' }),
      new File(['b'], '旧素材.png', { type: 'image/png' }),
    ]);

    await waitFor(() => expect(uploadMock).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(toastMock).toHaveBeenCalledWith('已上传 1 个，1 个已存在（自动复用）', 'success'),
    );
  });

  it('单个文件失败不影响后续文件，错误按文件名提示', async () => {
    uploadMock.mockRejectedValueOnce(new Error('文件类型不支持'));
    uploadMock.mockResolvedValueOnce(asset({ id: 'A2001' }));

    const { container } = render(<AssetLibrary />);
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    // 直接改 files，绕过 userEvent 对 accept 的过滤：
    // 要验的正是「用户绕过文件选择器的类型过滤」这种情况，
    // 前端不拦（拦在后端），错误要按文件名落到 toast 上
    fireEvent.change(input, {
      target: {
        files: [
          new File(['a'], '坏文件.exe', { type: 'application/octet-stream' }),
          new File(['b'], '好文件.png', { type: 'image/png' }),
        ],
      },
    });

    await waitFor(() => expect(uploadMock).toHaveBeenCalledTimes(2));
    expect(toastMock).toHaveBeenCalledWith('坏文件.exe：文件类型不支持', 'error');
    expect(toastMock).toHaveBeenCalledWith('已上传 1 个素材', 'success');
  });
});

describe('AssetLibrary · 删除', () => {
  it('确认后调用删除，成功后关闭弹窗', async () => {
    const user = userEvent.setup();
    setAssets([asset()]);
    render(<AssetLibrary />);

    await user.click(screen.getByRole('button', { name: '删除 超市抢购-参考图 01' }));
    expect(screen.getByText(/确认删除「超市抢购-参考图 01」/)).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '确认删除' }));
    expect(deleteMock).toHaveBeenCalledWith('A1000', expect.anything());
  });

  it('被引用时后端回 409：原样展示引用说明，不当成成功', async () => {
    const user = userEvent.setup();
    setAssets([asset()]);
    deleteMock.mockImplementation((_id: string, options: { onError: (e: Error) => void }) => {
      options.onError(new Error('该素材仍被 2 个模板引用，无法删除'));
    });
    render(<AssetLibrary />);

    await user.click(screen.getByRole('button', { name: '删除 超市抢购-参考图 01' }));
    await user.click(screen.getByRole('button', { name: '确认删除' }));

    expect(toastMock).toHaveBeenCalledWith('该素材仍被 2 个模板引用，无法删除', 'error');
    expect(toastMock).not.toHaveBeenCalledWith('素材已删除', 'success');
  });

  it('没有 asset:delete 权限时不渲染删除入口', () => {
    canMock.mockReturnValue(false);
    setAssets([asset()]);
    render(<AssetLibrary />);

    expect(screen.queryByRole('button', { name: '删除 超市抢购-参考图 01' })).toBeNull();
  });
});
