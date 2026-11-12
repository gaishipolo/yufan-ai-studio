/**
 * 资产库选择器：确认时交回的**顺序**与**完整性**。
 *
 * 这两件事都不是视觉问题，而是数据问题：
 *
 * - 顺序就是提交顺序（文本引用里直接等于 `{{Text N}}` 的编号）；
 * - 翻页 / 搜索之后看不到的已选项，不能被当成「用户解绑了」。
 */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { LibraryAssetOut } from '@/shared/api/schema';
import { LibraryPicker } from './LibraryPicker';

const { selection } = vi.hoisted(() => ({
  selection: { items: [] as LibraryAssetOut[], total: 0 },
}));

vi.mock('../api/library.queries', () => ({
  useLibrarySelection: () => ({
    data: { items: selection.items, total: selection.total },
    isPending: false,
  }),
}));

const A: LibraryAssetOut = {
  id: 'a',
  name: '甲方简报.md',
  kind: 'text',
  origin: 'upload',
  ownerId: 'u1',
  visibility: 'personal',
  state: 'active',
  createdAt: '2026-11-01T00:00:00Z',
};
const B: LibraryAssetOut = { ...A, id: 'b', name: '乙方简报.md' };
const C: LibraryAssetOut = { ...A, id: 'c', name: '丙方简报.md' };

function renderPicker(props: {
  value?: string[];
  valueItems?: LibraryAssetOut[];
  multiple?: boolean;
}) {
  const onConfirm = vi.fn();
  render(
    <LibraryPicker
      open
      multiple={props.multiple}
      kinds={['text']}
      value={props.value}
      valueItems={props.valueItems}
      onClose={vi.fn()}
      onConfirm={onConfirm}
    />,
  );
  return onConfirm;
}

const card = (name: string) => screen.getByRole('button', { name: new RegExp(name) });

beforeEach(() => {
  selection.items = [];
  selection.total = 0;
});

describe('LibraryPicker · 确认结果', () => {
  it('不在当前页的已选项不会丢：按 value 顺序原样带回', async () => {
    // 界面只查到 C，但用户之前绑的是 A、B（已翻页/被搜索过滤掉）
    selection.items = [C];
    selection.total = 1;
    const onConfirm = renderPicker({ value: ['a', 'b'], valueItems: [A, B] });

    await userEvent.setup().click(screen.getByRole('button', { name: '确认选择' }));

    expect(onConfirm).toHaveBeenCalledWith([A, B]);
  });

  it('新点进来的排在后面，不打乱已有编号', async () => {
    selection.items = [A, B, C];
    selection.total = 3;
    const onConfirm = renderPicker({ value: ['b'], valueItems: [B] });

    const user = userEvent.setup();
    await user.click(card('丙方简报'));
    await user.click(screen.getByRole('button', { name: '确认选择' }));

    expect(onConfirm).toHaveBeenCalledWith([B, C]);
  });

  it('取消勾选真的会移除（不能被 valueItems 的回显救回来）', async () => {
    selection.items = [A, B];
    selection.total = 2;
    const onConfirm = renderPicker({ value: ['a', 'b'], valueItems: [A, B] });

    const user = userEvent.setup();
    await user.click(card('甲方简报'));
    await user.click(screen.getByRole('button', { name: '确认选择' }));

    expect(onConfirm).toHaveBeenCalledWith([B]);
  });

  it('单选就是换一个，不把上一个一起带回去', async () => {
    selection.items = [A, B];
    selection.total = 2;
    const onConfirm = renderPicker({ multiple: false, value: ['a'], valueItems: [A] });

    const user = userEvent.setup();
    await user.click(card('乙方简报'));
    await user.click(screen.getByRole('button', { name: '确认选择' }));

    expect(onConfirm).toHaveBeenCalledWith([B]);
  });
});
