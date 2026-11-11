import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { TextAssetModal } from './TextAssetModal';

const { createMock, updateMock, readMock, toastMock } = vi.hoisted(() => ({
  createMock: vi.fn(),
  updateMock: vi.fn(),
  readMock: vi.fn(),
  toastMock: vi.fn(),
}));

vi.mock('@/entities/library/api/library.queries', () => ({
  useCreateTextAsset: () => ({ mutate: createMock, isPending: false }),
  useReadText: readMock,
  useUpdateText: () => ({ mutate: updateMock, isPending: false }),
}));

vi.mock('@/shared/lib/toast', () => ({ toast: toastMock }));

beforeEach(() => {
  createMock.mockReset();
  updateMock.mockReset();
  readMock.mockReset();
  toastMock.mockReset();
  readMock.mockReturnValue({ data: undefined, isPending: false, isError: false });
});

describe('TextAssetModal · 站内新建', () => {
  it('提交体带 name / content / extension，正文按纯文本原样下发', async () => {
    const user = userEvent.setup();
    render(<TextAssetModal open mode="create" onClose={vi.fn()} />);

    await user.type(screen.getByRole('textbox', { name: '文本资产名称' }), '分镜脚本');
    await user.type(screen.getByRole('textbox', { name: '文本正文' }), '雨夜街头');
    await user.selectOptions(screen.getByRole('combobox', { name: '文本格式' }), 'txt');
    await user.click(screen.getByRole('button', { name: '创建' }));

    expect(createMock.mock.calls[0][0]).toEqual({
      name: '分镜脚本',
      content: '雨夜街头',
      extension: 'txt',
    });
  });

  it('默认格式是 md；没填名称不提交', async () => {
    const user = userEvent.setup();
    render(<TextAssetModal open mode="create" onClose={vi.fn()} />);

    await user.type(screen.getByRole('textbox', { name: '文本正文' }), '正文');
    await user.click(screen.getByRole('button', { name: '创建' }));

    expect(createMock).not.toHaveBeenCalled();
    expect(screen.getByText('请填写名称')).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: '文本格式' })).toHaveValue('md');
  });
});

describe('TextAssetModal · 在线编辑', () => {
  it('保存带当前 expectedVersion，409 给明确的版本冲突提示', async () => {
    readMock.mockReturnValue({
      data: {
        id: 't1',
        name: '脚本.md',
        content: '旧正文',
        version: 7,
        sha256: 'abc',
        sizeBytes: 9,
        excerpt: '旧正文',
      },
      isPending: false,
      isError: false,
    });
    const user = userEvent.setup();
    render(<TextAssetModal open mode="edit" assetId="t1" assetName="脚本" onClose={vi.fn()} />);

    const body = screen.getByRole('textbox', { name: '文本正文' }) as HTMLTextAreaElement;
    expect(body.value).toBe('旧正文');
    await user.clear(body);
    await user.type(body, '新正文');
    await user.click(screen.getByRole('button', { name: '保存' }));

    expect(updateMock).toHaveBeenCalledWith(
      { id: 't1', body: expect.objectContaining({ content: '新正文', expectedVersion: 7 }) },
      expect.anything(),
    );

    const options = updateMock.mock.calls[0][1] as { onError: (error: Error) => void };
    act(() => {
      options.onError(Object.assign(new Error('conflict'), { code: 409 }));
    });
    await waitFor(() => expect(screen.getByText(/版本冲突/)).toBeInTheDocument());
  });
});
