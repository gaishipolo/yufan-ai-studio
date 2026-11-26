import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { LIBRARY_ASSET_DRAG_TYPE } from '@/shared/lib/libraryDrag';
import { LibraryUploadModal } from './LibraryUploadModal';

const { uploadMock, patchMock, toastMock } = vi.hoisted(() => ({
  uploadMock: vi.fn(),
  patchMock: vi.fn(),
  toastMock: vi.fn(),
}));

vi.mock('@/entities/library/api/library.queries', () => ({
  useUploadAsset: () => ({ mutateAsync: uploadMock, isPending: false }),
  usePatchAsset: () => ({ mutateAsync: patchMock, isPending: false }),
}));
vi.mock('./use-create-tag-inline', () => ({ useCreateTagInline: () => undefined }));
vi.mock('@/shared/lib/toast', () => ({ toast: toastMock }));

const TAGS = [
  { id: 't1', name: '产品', depth: 1, childCount: 0, createdAt: '2026-11-01T00:00:00Z' },
];

function file(name: string, type = ''): File {
  return new File(['x'], name, { type });
}

/**
 * 直接 change 而不是 userEvent.upload：后者会按 accept 属性**过滤掉**不合规的
 * 文件，而这里要验的正是「不合规的文件被组件自己拒掉」。
 */
function pick(fileToPick: File) {
  fireEvent.change(screen.getByLabelText('选择上传文件'), { target: { files: [fileToPick] } });
}

function dataTransfer(options: { types: string[]; files?: File[] }) {
  return {
    types: options.types,
    files: options.files ?? [],
    dropEffect: 'none',
    getData: vi.fn(() => ''),
    setData: vi.fn(),
  } as unknown as DataTransfer;
}

function dropZone() {
  return screen.getByRole('button', { name: /拖到这里/ });
}

beforeEach(() => {
  uploadMock.mockReset();
  patchMock.mockReset();
  toastMock.mockReset();
  uploadMock.mockResolvedValue({ id: 'a1' });
  patchMock.mockResolvedValue({ id: 'a1' });
});

afterEach(() => {
  vi.useRealTimers();
});

describe('LibraryUploadModal · 类型判定按后端扩展名白名单', () => {
  it('.gif / .svg 被就地拒掉（后端一律不收）', () => {
    render(<LibraryUploadModal open tags={[]} onClose={vi.fn()} />);

    pick(file('动图.gif', 'image/gif'));

    // 文件名照列，但标成失败并给出原因（后端一律不收 .gif/.svg）
    expect(screen.getByText(/只支持 .png/)).toBeInTheDocument();
    expect(screen.getByText('0 个待上传')).toBeInTheDocument();
  });

  it('浏览器给出空 file.type 的合法 .jpg 照收', () => {
    render(<LibraryUploadModal open tags={[]} onClose={vi.fn()} />);

    pick(file('产品图.jpg', ''));

    expect(screen.getByText('产品图.jpg')).toBeInTheDocument();
    expect(screen.getByText('1 个待上传')).toBeInTheDocument();
  });

  /* 视频与图片走**同一条**上传接口（`origin=upload`），只是 kind 不同。
     断言到 kind 这一层：只测「进了待上传」的话，kind 传错成 image 也能过，
     而后端会以 415 拒掉——前端看不出真因。 */
  it('.mp4 照收，并按 video 类型提交', async () => {
    const user = userEvent.setup();
    render(<LibraryUploadModal open tags={[]} onClose={vi.fn()} />);

    pick(file('参考片段.mp4', 'video/mp4'));
    expect(screen.getByText('1 个待上传')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '开始上传' }));

    await waitFor(() => expect(uploadMock).toHaveBeenCalledTimes(1));
    expect(uploadMock.mock.calls[0][0]).toMatchObject({ kind: 'video' });
  });
});

describe('LibraryUploadModal · 打标签失败不是上传失败', () => {
  it('文件已入库、只是标签没打上时，不报「没上传成功」', async () => {
    const user = userEvent.setup();
    patchMock.mockRejectedValue(new Error('标签已被删除'));
    render(<LibraryUploadModal open tags={TAGS} onClose={vi.fn()} />);

    pick(file('产品图.png', 'image/png'));
    await user.click(screen.getByRole('button', { name: '上传标签' }));
    await user.click(screen.getByRole('checkbox', { name: '产品' }));
    await user.click(screen.getByRole('button', { name: '开始上传' }));

    await waitFor(() => expect(screen.getByText(/标签没打上/)).toBeInTheDocument());
    expect(toastMock).toHaveBeenCalledWith('已上传 1 个文件', 'success');
    expect(toastMock).not.toHaveBeenCalledWith(expect.stringContaining('没上传成功'), 'error');
  });
});

describe('LibraryUploadModal · 拖拽投放', () => {
  /** 不 preventDefault 的话浏览器把投放当「打开文件」，整页导航到 file://，
      弹窗里已挑的文件和标签全丢。这条必须钉住。 */
  it('桌面文件拖进来先 preventDefault，再走与点选相同的 pick()', async () => {
    const user = userEvent.setup();
    render(<LibraryUploadModal open tags={[]} onClose={vi.fn()} />);
    const image = file('桌面拖来的.png', 'image/png');
    const transfer = dataTransfer({ types: ['Files'], files: [image] });

    // fireEvent 返回 false = dragover 事件被 preventDefault 了
    expect(fireEvent.dragOver(dropZone(), { dataTransfer: transfer })).toBe(false);
    fireEvent.drop(dropZone(), { dataTransfer: transfer });

    expect(screen.getByText('桌面拖来的.png')).toBeInTheDocument();
    expect(screen.getByText('1 个待上传')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '开始上传' }));
    await waitFor(() => expect(uploadMock).toHaveBeenCalledTimes(1));
    expect(uploadMock.mock.calls[0][0]).toMatchObject({ kind: 'image' });
  });

  /** 拖拽和点选必须共用 pick()：否则两条路各判一次扩展名 / 大小上限，
      迟早一边收 .gif 一边拒。 */
  it('拖进来的文件走同一套校验：.gif 被就地拒掉', () => {
    render(<LibraryUploadModal open tags={[]} onClose={vi.fn()} />);
    const transfer = dataTransfer({
      types: ['Files'],
      files: [file('动图.gif', 'image/gif')],
    });

    fireEvent.drop(dropZone(), { dataTransfer: transfer });

    expect(screen.getByText(/只支持 .png/)).toBeInTheDocument();
    expect(screen.getByText('0 个待上传')).toBeInTheDocument();
  });

  /** 从素材库卡片拖过来的资产是自定义 MIME，不是文件 —— 上传弹窗不该抢。 */
  it('素材库内部拖拽不接：不 preventDefault、也不进待上传列表', () => {
    render(<LibraryUploadModal open tags={[]} onClose={vi.fn()} />);
    const transfer = dataTransfer({ types: [LIBRARY_ASSET_DRAG_TYPE] });

    expect(fireEvent.dragOver(dropZone(), { dataTransfer: transfer })).toBe(true);
    fireEvent.drop(dropZone(), { dataTransfer: transfer });

    expect(screen.getByText('0 个待上传')).toBeInTheDocument();
    expect(uploadMock).not.toHaveBeenCalled();
  });

  it('上传中不接新的拖拽', async () => {
    const user = userEvent.setup();
    /* 让上传挂住：否则它瞬间完成、busy 回落，根本测不到「上传中」这一态。 */
    let finish!: (value: {id: string}) => void;
    uploadMock.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    render(<LibraryUploadModal open tags={[]} onClose={vi.fn()} />);
    pick(file('第一张.png', 'image/png'));
    await user.click(screen.getByRole('button', { name: '开始上传' }));
    await act(async () => {
      await Promise.resolve();
    });

    const transfer = dataTransfer({
      types: ['Files'],
      files: [file('第二张.png', 'image/png')],
    });
    /* 不收归不收，但 preventDefault 仍要在 —— 否则整页跳去 file:// */
    expect(fireEvent.dragOver(dropZone(), { dataTransfer: transfer })).toBe(false);
    fireEvent.drop(dropZone(), { dataTransfer: transfer });

    expect(screen.queryByText('第二张.png')).not.toBeInTheDocument();
    await act(async () => {
      finish({ id: 'a1' });
    });
  });
});

describe('LibraryUploadModal · 移除待上传文件', () => {
  /** 用户选错了文件要能去掉，不必关掉弹窗重来。 */
  it('待上传项有移除按钮，点一下从列表消失、计数跟着减', async () => {
    const user = userEvent.setup();
    render(<LibraryUploadModal open tags={[]} onClose={vi.fn()} />);
    pick(file('选错了.png', 'image/png'));

    expect(screen.getByText('1 个待上传')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '移除 选错了.png' }));

    expect(screen.queryByText('选错了.png')).not.toBeInTheDocument();
    expect(screen.getByText('0 个待上传')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '开始上传' })).toBeDisabled();
  });

  /** 移除中间一项后，`start()` 靠 `items.indexOf(target)` 定位逐条结果 ——
      索引错位会把 A 的进度 / 失败写到 B 上。 */
  it('移除中间一项后，剩下的文件仍各自按自己的名字与类型上传', async () => {
    const user = userEvent.setup();
    render(<LibraryUploadModal open tags={[]} onClose={vi.fn()} />);
    pick(file('A.png', 'image/png'));
    pick(file('B.mp4', 'video/mp4'));
    pick(file('C.png', 'image/png'));

    await user.click(screen.getByRole('button', { name: '移除 B.mp4' }));
    await user.click(screen.getByRole('button', { name: '开始上传' }));

    await waitFor(() => expect(uploadMock).toHaveBeenCalledTimes(2));
    expect(uploadMock.mock.calls.map((call) => call[0].file.name)).toEqual(['A.png', 'C.png']);
    expect(uploadMock.mock.calls.map((call) => call[0].kind)).toEqual(['image', 'image']);
  });

  /** 已上传 / 上传中 / 失败都没有「反悔」这回事，别给一个点了没反应的 X。 */
  it('只有待上传态有移除按钮', async () => {
    const user = userEvent.setup();
    patchMock.mockRejectedValue(new Error('标签已删'));
    render(<LibraryUploadModal open tags={[]} onClose={vi.fn()} />);
    pick(file('A.png', 'image/png'));

    await user.click(screen.getByRole('button', { name: '开始上传' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: '移除 A.png' })).not.toBeInTheDocument());
  });
});

describe('LibraryUploadModal · 自动关闭的定时器', () => {
  it('关掉再重开选新文件后，旧定时器不会把新文件清掉、也不会再关一次', async () => {
    vi.useFakeTimers();
    const onClose = vi.fn();
    const view = render(<LibraryUploadModal open tags={[]} onClose={onClose} />);

    pick(file('第一张.png', 'image/png'));
    fireEvent.click(screen.getByRole('button', { name: '开始上传' }));
    await act(async () => {
      await Promise.resolve();
    });
    expect(onClose).not.toHaveBeenCalled();

    /* 600ms 内用户自己关掉弹窗 */
    fireEvent.click(screen.getAllByRole('button', { name: '关闭' })[0]);
    expect(onClose).toHaveBeenCalledTimes(1);

    view.rerender(<LibraryUploadModal open tags={[]} onClose={onClose} />);
    pick(file('第二张.png', 'image/png'));

    act(() => {
      vi.advanceTimersByTime(700);
    });

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(screen.getByText('第二张.png')).toBeInTheDocument();
  });
});
