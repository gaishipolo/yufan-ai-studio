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
  top: within(screen.getByRole('group', { name: '一级标签' })),
  second: within(screen.getByRole('group', { name: '二级标签' })),
  third: within(screen.getByRole('group', { name: '三级标签' })),
});

function renderBar(selected: string[], onChange = vi.fn(), onClear = vi.fn()) {
  render(
    <LibraryTagBar tags={TAGS} selected={selected} onChange={onChange} onClear={onClear} />,
  );
  return { onChange, onClear };
}

describe('LibraryTagBar · 三级标签筛选', () => {
  it('三行分别是一级 / 二级 / 三级，各自按添加时间排序并带素材数', () => {
    renderBar([]);
    const { top, second, third } = rows();
    expect(top.getAllByRole('button').map((b) => b.textContent)).toEqual([
      '全部',
      '产品7',
      '角色3',
      '音乐2',
    ]);
    // 未选一级时显示全部二级，并用「父 / 子」区分同名系列
    expect(second.getAllByRole('button').map((b) => b.textContent)).toEqual([
      '全部',
      '产品 / 30305',
      '产品 / T402',
      '角色 / 菜菜3',
    ]);
    // 三级标签带整条链，看得出挂在哪个系列下
    expect(third.getAllByRole('button').map((b) => b.textContent)).toEqual([
      '全部',
      '产品 / 3030 / X1001',
    ]);
    expect(screen.queryByText(/^\+\d/)).not.toBeInTheDocument();
  });

  it('点一级只选中它（URL 里存的是一级 id）', async () => {
    const user = userEvent.setup();
    const { onChange } = renderBar([]);
    await user.click(rows().top.getByRole('button', { name: /^产品/ }));
    expect(onChange).toHaveBeenCalledWith(['t1']);
  });

  it('已选一级时第二行收起到该一级旗下，第三行也只列该一级子树下的三级', () => {
    renderBar(['t1']);
    const { second, third } = rows();
    expect(second.queryByRole('button', { name: /菜菜/ })).not.toBeInTheDocument();
    expect(second.getByRole('button', { name: /3030/ })).toBeInTheDocument();
    expect(second.getByRole('button', { name: /T40/ })).toBeInTheDocument();
    expect(third.getByRole('button', { name: /X100/ })).toBeInTheDocument();
  });

  it('点旗下二级会把父一级换掉，避免 OR 叠加后等于没筛', async () => {
    const user = userEvent.setup();
    const { onChange } = renderBar(['t1']);
    await user.click(rows().second.getByRole('button', { name: /3030/ }));
    expect(onChange).toHaveBeenCalledWith(['t11']);
  });

  it('选中二级后第三行收起到该二级下，刷新后三行仍对得上', () => {
    renderBar(['t11']);
    const { second, third } = rows();
    expect(second.getByRole('button', { name: /T40/ })).toBeInTheDocument();
    expect(second.queryByRole('button', { name: /菜菜/ })).not.toBeInTheDocument();
    expect(third.getByRole('button', { name: /X100/ })).toBeInTheDocument();
  });

  it('选三级会把同链的一级和二级一并换掉：一条链上只选一层', async () => {
    const user = userEvent.setup();
    const { onChange } = renderBar(['t1', 't11']);
    await user.click(rows().third.getByRole('button', { name: /X100/ }));
    expect(onChange).toHaveBeenCalledWith(['t31']);
  });

  it('可跨组多选：产品下 3030 与角色一级并存', async () => {
    const user = userEvent.setup();
    const { onChange } = renderBar(['t11']);
    await user.click(rows().top.getByRole('button', { name: /^角色/ }));
    expect(onChange).toHaveBeenCalledWith(['t11', 't2']);
  });

  it('三行都是「行标题 → 搜索框 → 全部」，输入框上下对齐', () => {
    renderBar([]);
    for (const [rowName, searchName, allName] of [
      ['一级标签', '搜索一级标签', '全部标签'],
      ['二级标签', '搜索二级标签', '全部二级标签'],
      ['三级标签', '搜索三级标签', '全部三级标签'],
    ] as const) {
      const row = within(screen.getByRole('group', { name: rowName }));
      const input = row.getByRole('searchbox', { name: searchName });
      expect(input.previousElementSibling?.tagName).toBe('SPAN');
      expect(input.nextElementSibling?.getAttribute('aria-label')).toBe(allName);
    }
  });

  it('每行各自搜索，只过滤本行', async () => {
    const user = userEvent.setup();
    renderBar([]);
    const { top, second } = rows();
    await user.type(top.getByRole('searchbox', { name: '搜索一级标签' }), '音');
    expect(top.queryByRole('button', { name: /^产品/ })).not.toBeInTheDocument();
    expect(top.getByRole('button', { name: /^音乐/ })).toBeInTheDocument();
    expect(second.getByRole('button', { name: /3030/ })).toBeInTheDocument();
  });

  it('第一行「全部」清空所有筛选', async () => {
    const user = userEvent.setup();
    const { onClear } = renderBar(['t1', 't21']);
    await user.click(screen.getByRole('button', { name: '全部标签' }));
    expect(onClear).toHaveBeenCalledTimes(1);
  });

  it('第二行「全部」只清二级及以下，一级不动', async () => {
    const user = userEvent.setup();
    const { onChange } = renderBar(['t1', 't21']);
    await user.click(screen.getByRole('button', { name: '全部二级标签' }));
    expect(onChange).toHaveBeenCalledWith(['t1']);
  });

  it('第三行「全部」只清三级', async () => {
    const user = userEvent.setup();
    const { onChange } = renderBar(['t1', 't11', 't31']);
    await user.click(screen.getByRole('button', { name: '全部三级标签' }));
    expect(onChange).toHaveBeenCalledWith(['t1', 't11']);
  });

  it('没有标签时给一句说明，而不是留一块空白', () => {
    render(<LibraryTagBar tags={[]} selected={[]} onChange={vi.fn()} onClear={vi.fn()} />);
    expect(screen.getByText(/还没有标签/)).toBeInTheDocument();
  });
});
