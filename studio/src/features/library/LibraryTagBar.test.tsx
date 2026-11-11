import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { LibraryTagStatOut } from '@/shared/api/schema';
import { LibraryTagBar } from './LibraryTagBar';

/** 故意乱序：两级各自要按 createdAt 排，不能照搬接口给的顺序。 */
const TAGS: LibraryTagStatOut[] = [
  { id: 't21', name: '菜菜', parentId: 't2', parentName: '角色', depth: 2, assetCount: 3, childCount: 0, createdAt: '2026-11-09T10:06:00Z' },
  { id: 't2', name: '角色', assetCount: 3, childCount: 1, createdAt: '2026-11-09T10:02:00Z' },
  { id: 't12', name: 'T35', parentId: 't1', parentName: '产品', depth: 2, assetCount: 2, childCount: 0, createdAt: '2026-11-09T10:05:00Z' },
  { id: 't1', name: '产品', assetCount: 7, childCount: 2, createdAt: '2026-11-09T10:01:00Z' },
  { id: 't3', name: '音乐', assetCount: 2, childCount: 0, createdAt: '2026-11-09T10:03:00Z' },
  { id: 't11', name: '3030', parentId: 't1', parentName: '产品', depth: 2, assetCount: 5, childCount: 0, createdAt: '2026-11-09T10:04:00Z' },
];

const rows = () => ({
  top: within(screen.getByRole('group', { name: '一级标签' })),
  child: within(screen.getByRole('group', { name: '二级标签' })),
});

function renderBar(selected: string[], onChange = vi.fn(), onClear = vi.fn()) {
  render(
    <LibraryTagBar tags={TAGS} selected={selected} onChange={onChange} onClear={onClear} />,
  );
  return { onChange, onClear };
}

describe('LibraryTagBar · 两级标签筛选', () => {
  it('第一行一级、第二行二级，各自按添加时间排序并带素材数', () => {
    renderBar([]);
    const { top, child } = rows();
    expect(top.getAllByRole('button').map((b) => b.textContent)).toEqual([
      '全部',
      '产品7',
      '角色3',
      '音乐2',
    ]);
    // 未选一级时显示全部二级，并用「父 / 子」区分同名系列
    expect(child.getAllByRole('button').map((b) => b.textContent)).toEqual([
      '全部',
      '产品 / 30305',
      '产品 / T352',
      '角色 / 菜菜3',
    ]);
    expect(screen.queryByText(/^\+\d/)).not.toBeInTheDocument();
  });

  it('点一级只选中它（URL 里存的是一级 id）', async () => {
    const user = userEvent.setup();
    const { onChange } = renderBar([]);
    await user.click(rows().top.getByRole('button', { name: /^产品/ }));
    expect(onChange).toHaveBeenCalledWith(['t1']);
  });

  it('已选一级时第二行收起到该一级旗下，别的系列不再出现', () => {
    renderBar(['t1']);
    const { child } = rows();
    expect(child.queryByRole('button', { name: /菜菜/ })).not.toBeInTheDocument();
    expect(child.getByRole('button', { name: /3030/ })).toBeInTheDocument();
    expect(child.getByRole('button', { name: /T35/ })).toBeInTheDocument();
  });

  it('点旗下二级会把父一级换掉，避免 OR 叠加后等于没筛', async () => {
    const user = userEvent.setup();
    const { onChange } = renderBar(['t1']);
    await user.click(rows().child.getByRole('button', { name: /3030/ }));
    expect(onChange).toHaveBeenCalledWith(['t11']);
  });

  it('currentParent 从已选二级的 parentId 派生，刷新后两行仍对得上', () => {
    renderBar(['t11']);
    const { child } = rows();
    expect(child.getByRole('button', { name: /T35/ })).toBeInTheDocument();
    expect(child.queryByRole('button', { name: /菜菜/ })).not.toBeInTheDocument();
  });

  it('可跨组多选：产品下 3030 与角色一级并存', async () => {
    const user = userEvent.setup();
    const { onChange } = renderBar(['t11']);
    await user.click(rows().top.getByRole('button', { name: /^角色/ }));
    expect(onChange).toHaveBeenCalledWith(['t11', 't2']);
  });

  it('每行各自搜索，只过滤本行', async () => {
    const user = userEvent.setup();
    renderBar([]);
    const { top, child } = rows();
    await user.type(top.getByRole('searchbox', { name: '搜索一级标签' }), '音');
    expect(top.queryByRole('button', { name: /^产品/ })).not.toBeInTheDocument();
    expect(top.getByRole('button', { name: /^音乐/ })).toBeInTheDocument();
    expect(child.getByRole('button', { name: /3030/ })).toBeInTheDocument();
  });

  it('第一行「全部」清空所有筛选；第二行「全部」只清二级', async () => {
    const user = userEvent.setup();
    const { onChange, onClear } = renderBar(['t1', 't21']);
    await user.click(screen.getByRole('button', { name: '全部二级标签' }));
    expect(onChange).toHaveBeenCalledWith(['t1']);
    await user.click(screen.getByRole('button', { name: '全部标签' }));
    expect(onClear).toHaveBeenCalledTimes(1);
  });

  it('没有标签时给一句说明，而不是留一块空白', () => {
    render(<LibraryTagBar tags={[]} selected={[]} onChange={vi.fn()} onClear={vi.fn()} />);
    expect(screen.getByText(/还没有标签/)).toBeInTheDocument();
  });
});
