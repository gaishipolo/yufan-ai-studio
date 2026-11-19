import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { LibraryBulkBar, type BulkKind } from './LibraryBulkBar';

const TAGS = [
  { id: 't1', name: '产品', depth: 1, childCount: 0, createdAt: '2026-11-01T00:00:00Z' },
];

function renderBar(overrides: Partial<React.ComponentProps<typeof LibraryBulkBar>> = {}) {
  const onAction = vi.fn<(kind: BulkKind, payload?: { tagIds?: string[] }) => void>();
  const onClear = vi.fn();
  const view = render(
    <LibraryBulkBar
      view="all"
      count={2}
      tags={TAGS}
      onClear={onClear}
      onAction={onAction}
      {...overrides}
    />,
  );
  return { onAction, onClear, rerender: view.rerender };
}

describe('LibraryBulkBar · 入口收敛', () => {
  it('小组 / 团队入口不给「移入回收站」：那两处的素材是共享的，后端必然拒', () => {
    renderBar({ view: 'group' });
    expect(screen.queryByRole('button', { name: '移入回收站' })).not.toBeInTheDocument();
    renderBar({ view: 'team' });
    expect(screen.queryByRole('button', { name: '移入回收站' })).not.toBeInTheDocument();
  });

  it('个人 / 全部入口照旧给删除', () => {
    renderBar({ view: 'personal' });
    expect(screen.getByRole('button', { name: '移入回收站' })).toBeInTheDocument();
  });
});

describe('LibraryBulkBar · 清空后不留上一批的标签', () => {
  it('清空（选中数归零）会把打标签面板和已选标签一起复位', async () => {
    const user = userEvent.setup();
    const { rerender } = renderBar();


    await user.click(screen.getByRole('button', { name: '打标签' }));
    await user.click(screen.getByRole('button', { name: '批量添加标签' }));
    await user.click(screen.getByRole('checkbox', { name: '产品' }));
    expect(screen.getByRole('button', { name: '应用到 2 项' })).toBeEnabled();

    /* 组件只是被隐藏、并没有卸载：选中数归零时必须自己复位，
       否则「清空」后再勾一批，旧的标签会被静默打到新素材上。 */
    rerender(
      <LibraryBulkBar view="all" count={0} tags={TAGS} onClear={vi.fn()} onAction={vi.fn()} />,
    );

    expect(screen.queryByRole('button', { name: '打标签' })).not.toBeInTheDocument();

    rerender(
      <LibraryBulkBar view="all" count={3} tags={TAGS} onClear={vi.fn()} onAction={vi.fn()} />,
    );
    await user.click(screen.getByRole('button', { name: '打标签' }));
    // 面板回来了，但上一批选的标签没跟过来（否则会被静默打到新素材上）
    expect(screen.getByRole('button', { name: '批量添加标签' })).toHaveTextContent(
      '选择要添加的标签',
    );
    expect(screen.getByRole('button', { name: '应用到 3 项' })).toBeDisabled();
  });
});

describe('LibraryBulkBar · 组建封装', () => {
  it('个人 / 全部入口给「组建封装」，点了把动作交回上层（不直接发请求）', async () => {
    const user = userEvent.setup();
    const { onAction } = renderBar();
    await user.click(screen.getByRole('button', { name: '组建封装' }));
    expect(onAction).toHaveBeenCalledWith('pack');
  });

  it('草稿箱 / 回收站没有「组建封装」：那里的东西还不是库内容', () => {
    renderBar({ view: 'drafts' });
    expect(screen.queryByRole('button', { name: '组建封装' })).not.toBeInTheDocument();
    renderBar({ view: 'trash' });
    expect(screen.queryByRole('button', { name: '组建封装' })).not.toBeInTheDocument();
  });
});
