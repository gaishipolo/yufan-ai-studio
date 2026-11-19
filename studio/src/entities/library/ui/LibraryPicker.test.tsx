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

describe('LibraryPicker · 不可选的项', () => {
  /* 调用方知道「为什么不能选」（已经属于别的封装），组件只负责把它说出来。
     只传一组禁用 id 的话，卡片上只能写「不可用」—— 用户看不出下一步该干什么。 */
  it('显示原因、点不动，且不会被选进去', async () => {
    const user = userEvent.setup();
    selection.items = [
      { ...A, id: 'taken', name: '已被收走的.png', kind: 'image', packName: '6036 黑' },
      { ...A, id: 'free', name: '还能选的.png', kind: 'image' },
    ];
    selection.total = 2;
    const onConfirm = vi.fn();
    render(
      <LibraryPicker
        open
        multiple
        kinds={['image']}
        unavailableReason={(asset) =>
          asset.packName ? `已在封装「${asset.packName}」里` : null
        }
        onClose={vi.fn()}
        onConfirm={onConfirm}
      />,
    );

    const blocked = screen.getByRole('button', { name: /已被收走的/ });
    expect(blocked).toBeDisabled();
    // 原因就地显示在「它是什么类型」那一行：这时类型不重要，原因才是下一步
    expect(screen.getByText('已在封装「6036 黑」里')).toBeInTheDocument();

    await user.click(blocked);
    expect(onConfirm).not.toHaveBeenCalledWith(
      expect.arrayContaining([expect.objectContaining({ id: 'taken' })]),
    );

    await user.click(screen.getByRole('button', { name: /还能选的/ }));
    await user.click(screen.getByRole('button', { name: /确认/ }));
    const confirmed = onConfirm.mock.calls[0]?.[0] as LibraryAssetOut[];
    expect(confirmed.map((item) => item.id)).toEqual(['free']);
  });

  it('不传原因函数时一切照旧（可选）', async () => {
    const user = userEvent.setup();
    selection.items = [{ ...A, id: 'taken', name: '普通图.png', kind: 'image' }];
    selection.total = 1;
    const onConfirm = vi.fn();
    render(
      <LibraryPicker open multiple kinds={['image']} onClose={vi.fn()} onConfirm={onConfirm} />,
    );
    await user.click(screen.getByRole('button', { name: /普通图/ }));
    await user.click(screen.getByRole('button', { name: /确认/ }));
    expect((onConfirm.mock.calls[0]?.[0] as LibraryAssetOut[]).map((i) => i.id)).toEqual(['taken']);
  });
});
