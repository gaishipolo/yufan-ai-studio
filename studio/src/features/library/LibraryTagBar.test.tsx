import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { LibraryTagStatOut } from '@/shared/api/schema';
import { LibraryTagBar } from './LibraryTagBar';

/** 故意乱序：组件要按 createdAt 排，不能照搬接口给的顺序。 */
const TAGS: LibraryTagStatOut[] = [
  { id: 't3', name: '场景', assetCount: 0, createdAt: '2026-11-09T10:00:00Z' },
  { id: 't1', name: '人物', assetCount: 7, createdAt: '2026-11-01T10:00:00Z' },
  { id: 't2', name: '产品', assetCount: 2, createdAt: '2026-11-05T10:00:00Z' },
];

describe('LibraryTagBar', () => {
  it('摊开全部标签（含 0 条），带素材数并按添加时间排序', () => {
    render(<LibraryTagBar tags={TAGS} selected={[]} onToggle={vi.fn()} onClear={vi.fn()} />);

    const buttons = screen.getAllByRole('button');
    // 「全部」+ 三个标签；没有「+N」这种折叠
    expect(buttons).toHaveLength(4);
    expect(buttons.slice(1).map((button) => button.textContent)).toEqual([
      '人物7',
      '产品2',
      '场景0',
    ]);
    expect(screen.queryByText(/^\+\d/)).not.toBeInTheDocument();
  });

  it('点击标签筛选，选中态与「全部」互斥，清除筛选可退出', async () => {
    const user = userEvent.setup();
    const onToggle = vi.fn();
    const onClear = vi.fn();
    render(
      <LibraryTagBar tags={TAGS} selected={['t2']} onToggle={onToggle} onClear={onClear} />,
    );

    const product = screen.getByRole('button', { name: /产品/ });
    expect(product).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: '全部' })).toHaveAttribute('aria-pressed', 'false');

    await user.click(product);
    expect(onToggle).toHaveBeenCalledWith('t2');
    await user.click(screen.getByRole('button', { name: /清除筛选/ }));
    expect(onClear).toHaveBeenCalledTimes(1);
  });

  it('没有标签时给一句说明，而不是留一块空白', () => {
    render(<LibraryTagBar tags={[]} selected={[]} onToggle={vi.fn()} onClear={vi.fn()} />);
    expect(screen.getByText(/还没有标签/)).toBeInTheDocument();
  });
});
