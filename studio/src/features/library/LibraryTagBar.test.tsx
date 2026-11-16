import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { LibraryTagStatOut } from '@/shared/api/schema';
import { LibraryTagBar } from './LibraryTagBar';

/** 故意乱序：每一层各自要按 createdAt 排，不能照搬接口给的顺序。
 *  树是三级的：产品 → 3030 → X100，产品 → T40，角色 → 菜菜。 */
const TAGS: LibraryTagStatOut[] = [
  { id: 't31', name: 'X100', parentId: 't11', parentName: '产品 / 3030', depth: 3, assetCount: 1, childCount: 0, createdAt: '2026-11-09T10:07:00Z' },
  { id: 't21', name: '菜菜', parentId: 't2', parentName: '角色', depth: 2, assetCount: 3, childCount: 0, createdAt: '2026-11-09T10:06:00Z' },
  { id: 't2', name: '角色', assetCount: 3, childCount: 1, createdAt: '2026-11-09T10:02:00Z' },
  { id: 't12', name: 'T40', parentId: 't1', parentName: '产品', depth: 2, assetCount: 2, childCount: 0, createdAt: '2026-11-09T10:05:00Z' },
  { id: 't1', name: '产品', assetCount: 7, childCount: 2, createdAt: '2026-11-09T10:01:00Z' },
  { id: 't3', name: '音乐', assetCount: 2, childCount: 0, createdAt: '2026-11-09T10:03:00Z' },
  { id: 't11', name: '3030', parentId: 't1', parentName: '产品', depth: 2, assetCount: 5, childCount: 1, createdAt: '2026-11-09T10:04:00Z' },
];

const rows = () => ({
  top: within(screen.getByRole('group', {name: '一级标签'})),
  second: within(screen.getByRole('group', {name: '二级标签'})),
  third: within(screen.getByRole('group', {name: '三级标签'})),
});

function renderBar(selected: string[], onChange = vi.fn(), onClear = vi.fn()) {
  render(
    <LibraryTagBar tags={TAGS} selected={selected} onChange={onChange} onClear={onClear} />,
  );
  return { onChange, onClear };
}

describe('LibraryTagBar · 三级标签任意组合', () => {
  it('三行分别是一级 / 二级 / 三级，各自按添加时间排序并带素材数', () => {
    renderBar([]);
    const { top, second, third } = rows();
    expect(top.getAllByRole('button').map((b) => b.textContent)).toEqual([
      '全部',
      '产品7',
      '角色3',
      '音乐2',
    ]);
    // 二级带父名，三级带整条链：分得出同一个名字挂在哪条链上
    expect(second.getAllByRole('button').map((b) => b.textContent)).toEqual([
      '全部',
      '产品 / 30305',
      '产品 / T402',
      '角色 / 菜菜3',
    ]);
    expect(third.getAllByRole('button').map((b) => b.textContent)).toEqual([
      '全部',
      '产品 / 3030 / X1001',
    ]);
  });

  it('三行始终列全部标签：跨链多选时另一条链的词也要点得到', () => {
    renderBar(['t11']);
    const { top, second, third } = rows();
    // 选了一级也不收拢 —— 收起来就点不到「角色 / 菜菜」了
    expect(top.getByRole('button', {name: /^角色/})).toBeInTheDocument();
    expect(second.getByRole('button', {name: /菜菜/})).toBeInTheDocument();
    expect(third.getByRole('button', {name: /X100/})).toBeInTheDocument();
  });

  it('点一级只选中它（URL 里存的是一级 id）', async () => {
    const user = userEvent.setup();
    const { onChange } = renderBar([]);
    await user.click(rows().top.getByRole('button', {name: /^产品/}));
    expect(onChange).toHaveBeenCalledWith(['t1']);
  });

  it('点二级不再把父一级踢掉：三个级别可以任意组合', async () => {
    const user = userEvent.setup();
    const { onChange } = renderBar(['t1']);
    await user.click(rows().second.getByRole('button', {name: /3030/}));
    expect(onChange).toHaveBeenCalledWith(['t1', 't11']);
  });

  it('点三级也保留同链的一级与二级', async () => {
    const user = userEvent.setup();
    const { onChange } = renderBar(['t1', 't11']);
    await user.click(rows().third.getByRole('button', {name: /X100/}));
    expect(onChange).toHaveBeenCalledWith(['t1', 't11', 't31']);
  });

  it('再点一次取消选择（同一枚胶囊来回切）', async () => {
    const user = userEvent.setup();
    const { onChange } = renderBar(['t11']);
    await user.click(rows().second.getByRole('button', {name: /3030/}));
    expect(onChange).toHaveBeenCalledWith([]);
  });

  it('可跨链多选：产品下 3030 与角色一级并存', async () => {
    const user = userEvent.setup();
    const { onChange } = renderBar(['t11']);
    await user.click(rows().top.getByRole('button', {name: /^角色/}));
    expect(onChange).toHaveBeenCalledWith(['t11', 't2']);
  });

  it('三行都是「行标题 → 搜索框 → 全部」，输入框上下对齐', () => {
    renderBar([]);
    for (const [rowName, searchName] of [
      ['一级标签', '搜索一级标签'],
      ['二级标签', '搜索二级标签'],
      ['三级标签', '搜索三级标签'],
    ] as const) {
      const row = within(screen.getByRole('group', {name: rowName}));
      const input = row.getByRole('searchbox', {name: searchName});
      expect(input.previousElementSibling?.tagName).toBe('SPAN');
      // 搜索框后面紧跟「全部」按钮
      expect(input.nextElementSibling?.textContent).toBe('全部');
    }
  });

  it('每行各自搜索，只过滤本行', async () => {
    const user = userEvent.setup();
    renderBar([]);
    const { top, second } = rows();
    await user.type(top.getByRole('searchbox', {name: '搜索一级标签'}), '音');
    expect(top.queryByRole('button', {name: /^产品/})).not.toBeInTheDocument();
    expect(top.getByRole('button', {name: /^音乐/})).toBeInTheDocument();
    expect(second.getByRole('button', {name: /3030/})).toBeInTheDocument();
  });

  it('每一行的「全部」都清空所有层级（不再只清自己那一层）', async () => {
    const user = userEvent.setup();
    const { onClear } = renderBar(['t1', 't11', 't31']);
    const { top, second, third } = rows();
    await user.click(top.getByRole('button', {name: '全部标签'}));
    await user.click(second.getByRole('button', {name: '全部标签（二级行）'}));
    await user.click(third.getByRole('button', {name: '全部标签（三级行）'}));
    expect(onClear).toHaveBeenCalledTimes(3);
  });

  it('没有任何选中时，「全部」是按下的', () => {
    renderBar([]);
    expect(rows().top.getByRole('button', {name: '全部标签'})).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });

  it('没有标签时给一句说明，而不是留一块空白', () => {
    render(<LibraryTagBar tags={[]} selected={[]} onChange={vi.fn()} onClear={vi.fn()} />);
    expect(screen.getByText(/还没有标签/)).toBeInTheDocument();
  });
});
