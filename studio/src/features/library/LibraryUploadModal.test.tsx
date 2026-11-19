import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
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
