import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { LibraryPackModal } from './LibraryPackModal';

const { createMock, toastMock } = vi.hoisted(() => ({
  createMock: vi.fn(),
  toastMock: vi.fn(),
}));

const ITEMS = [
  { id: 'a1', name: '正面.png', kind: 'image', sizeBytes: 10, coverUrl: null },
  { id: 'a2', name: '三视图.png', kind: 'image', sizeBytes: 20, coverUrl: null },
];

vi.mock('@/entities/library/api/library.queries', () => ({
  useCreatePack: () => ({ mutate: createMock, isPending: false }),
  useLibrarySelection: () => ({
    data: { items: ITEMS, total: ITEMS.length, page: 1, pageSize: 60 },
    isPending: false,
    isError: false,
    refetch: vi.fn(),
  }),
}));

vi.mock('@/shared/lib/toast', () => ({ toast: toastMock }));

beforeEach(() => {
  createMock.mockReset();
  toastMock.mockReset();
});

/**
 * 建封装时给成员声明用途：模板库把封装当参考图用时，`{{Mixed N}}` 是按用途取位的。
 * 页面上没有这个入口，B 端选中封装就必然撞「没有声明可用途」——而那句话能做的动作
 * 恰好就是打开这个对话框。
 */
describe('LibraryPackModal · 成员用途', () => {
  it('提交 items（顺序 = 勾选顺序），用途跟着成员走；没选的写空串', async () => {
    const user = userEvent.setup();
    render(<LibraryPackModal open preselected={[]} onClose={vi.fn()} />);

    await user.type(screen.getByRole('textbox', { name: '封装名称' }), '产品三视图');
    // 先勾三视图再勾正面：成员顺序必须是勾选顺序，不能按列表顺序重排
    await user.click(screen.getByRole('button', { name: /三视图/ }));
    await user.click(screen.getByRole('button', { name: /正面/ }));

    await user.selectOptions(screen.getByRole('combobox', { name: '第 1 项用途' }), 'multiview');
    await user.selectOptions(screen.getByRole('combobox', { name: '第 2 项用途' }), 'front');
    await user.click(screen.getByRole('button', { name: '创建封装' }));

    expect(createMock.mock.calls[0][0]).toEqual({
      name: '产品三视图',
      items: [
        { assetId: 'a2', role: 'multiview' },
        { assetId: 'a1', role: 'front' },
      ],
    });
  });

  it('没声明用途时如实写空串，不按位置猜主图', async () => {
    const user = userEvent.setup();
    render(<LibraryPackModal open preselected={[]} onClose={vi.fn()} />);

    await user.type(screen.getByRole('textbox', { name: '封装名称' }), '只归档');
    await user.click(screen.getByRole('button', { name: /正面/ }));
    await user.click(screen.getByRole('button', { name: '创建封装' }));

    expect(createMock.mock.calls[0][0].items).toEqual([{ assetId: 'a1', role: '' }]);
  });

  it('取消勾选后用途条目一起消失，不留下孤儿设置', async () => {
    const user = userEvent.setup();
    render(<LibraryPackModal open preselected={[]} onClose={vi.fn()} />);

    await user.type(screen.getByRole('textbox', { name: '封装名称' }), '改主意');
    await user.click(screen.getByRole('button', { name: /正面/ }));
    expect(screen.getByRole('combobox', { name: '第 1 项用途' })).toBeTruthy();
    await user.click(screen.getByRole('button', { name: /正面/ }));
    expect(screen.queryByRole('combobox', { name: '第 1 项用途' })).toBeNull();
  });
});
