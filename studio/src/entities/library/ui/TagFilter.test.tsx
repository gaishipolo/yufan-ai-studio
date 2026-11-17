import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { LibraryTagStatOut } from '@/shared/api/schema';
import { TagFilter } from './TagFilter';

/**
 * 标签筛选条（选择器用）。
 *
 * 两级模型：一级是维度（产品 / 角色），二级是取值（3030 / 菜菜）。
 *
 * 模板库那侧（`hideTopLevel` + `excludeIds`）：
 *
 * - 一级维度行**不显示** —— 那只是分组头，范围已经由候选集定死了；
 * - 候选集里的标签**不显示** —— 它就是范围本身，列出来点了等于没筛。
 */
const TAGS: LibraryTagStatOut[] = [
  { id: 't1', name: '产品', assetCount: 7, childCount: 2, createdAt: '2026-11-09T10:01:00Z' },
  { id: 't2', name: '角色', assetCount: 3, childCount: 1, createdAt: '2026-11-09T10:02:00Z' },
  { id: 't11', name: '3030', parentId: 't1', parentName: '产品', depth: 2, assetCount: 5, childCount: 0, createdAt: '2026-11-09T10:04:00Z' },
  { id: 't12', name: 'T40', parentId: 't1', parentName: '产品', depth: 2, assetCount: 2, childCount: 0, createdAt: '2026-11-09T10:05:00Z' },
  { id: 't21', name: '菜菜', parentId: 't2', parentName: '角色', depth: 2, assetCount: 3, childCount: 0, createdAt: '2026-11-09T10:06:00Z' },
];

describe('TagFilter · 标签筛选条', () => {
  it('默认两行：一级维度 / 取值，取值只显示短名', () => {
    render(<TagFilter tags={TAGS} value={[]} onChange={vi.fn()} />);
    const top = within(screen.getByRole('group', {name: '一级标签'}));
    const second = within(screen.getByRole('group', {name: '取值标签'}));
    expect(top.getAllByRole('button').map((b) => b.textContent)).toEqual([
      '产品',
      '角色',
    ]);
    // 显示短名（「3030」而不是「产品 / 3030」），完整路径在 title 里
    expect(second.getAllByRole('button').map((b) => b.textContent)).toEqual([
      '3030',
      'T40',
      '菜菜',
    ]);
    expect(second.getByRole('button', {name: '3030'})).toHaveAttribute(
      'title',
      '产品 / 3030',
    );
  });

  it('只列这批素材身上实际有的标签（计数为 0 的不出现）', () => {
    /* 词表里「菜菜」没有素材（assetCount=0）→ 不该出现；
       用户点它只会得到空列表。 */
    const scoped = TAGS.map((tag) =>
      tag.id === 't21' ? { ...tag, assetCount: 0 } : tag,
    );
    render(<TagFilter tags={scoped} onlyWithAssets value={[]} onChange={vi.fn()} />);
    const second = within(screen.getByRole('group', {name: '取值标签'}));
    expect(second.queryByRole('button', {name: '菜菜'})).not.toBeInTheDocument();
    expect(second.getByRole('button', {name: '3030'})).toBeInTheDocument();
  });

  it('不收窄时按全词表列（资产库标签栏那样）', () => {
    render(<TagFilter tags={TAGS} value={[]} onChange={vi.fn()} />);
    const second = within(screen.getByRole('group', {name: '取值标签'}));
    expect(second.getByRole('button', {name: '菜菜'})).toBeInTheDocument();
  });

  it('点一下选中、再点一下取消（多选语义）', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const { rerender } = render(
      <TagFilter tags={TAGS} value={[]} onChange={onChange} />,
    );
    const top = within(screen.getByRole('group', {name: '一级标签'}));
    await user.click(top.getByRole('button', {name: '产品'}));
    expect(onChange).toHaveBeenCalledWith(['t1']);

    rerender(<TagFilter tags={TAGS} value={['t1']} onChange={onChange} />);
    await user.click(top.getByRole('button', {name: '产品'}));
    expect(onChange).toHaveBeenCalledWith([]);
  });

  it('取值行随选中的一级收窄：点了「产品」就只剩产品的取值', () => {
    render(<TagFilter tags={TAGS} value={['t1']} onChange={vi.fn()} />);
    const second = within(screen.getByRole('group', {name: '取值标签'}));
    expect(second.getAllByRole('button').map((b) => b.textContent)).toEqual([
      '3030',
      'T40',
    ]);
  });

  it('挑了别的标签时维度标签不会被踢掉（收敛交给调用方）', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<TagFilter tags={TAGS} value={['t1']} onChange={onChange} />);
    const second = within(screen.getByRole('group', {name: '取值标签'}));
    await user.click(second.getByRole('button', {name: '3030'}));
    expect(onChange).toHaveBeenCalledWith(['t1', 't11']);
  });

  it('挑了标签就给出「清除」入口', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<TagFilter tags={TAGS} value={['t1']} onChange={onChange} />);
    await user.click(screen.getByRole('button', {name: '清除标签筛选'}));
    expect(onChange).toHaveBeenCalledWith([]);
  });
});

describe('TagFilter · 模板库那一侧（hideTopLevel + excludeIds）', () => {
  it('一级维度行完全不显示', () => {
    render(
      <TagFilter tags={TAGS} hideTopLevel excludeIds={['t2']} value={[]} onChange={vi.fn()} />,
    );
    expect(screen.queryByRole('group', {name: '一级标签'})).not.toBeInTheDocument();
  });

  it('候选集里的标签不作为选项出现（它就是范围，点了等于没筛）', () => {
    render(
      <TagFilter tags={TAGS} hideTopLevel excludeIds={['t21']} value={[]} onChange={vi.fn()} />,
    );
    const second = within(screen.getByRole('group', {name: '取值标签'}));
    expect(second.queryByRole('button', {name: '菜菜'})).not.toBeInTheDocument();
    expect(second.getByRole('button', {name: '3030'})).toBeInTheDocument();
  });

  it('排除一级维度后，它的取值照样列得出来（维度解析不能被排除打断）', () => {
    /* 「产品」本身被排除，但它下面的 3030 / T40 还得能挑 ——
       排除只发生在展示层，不能把父链剪断。 */
    render(
      <TagFilter tags={TAGS} hideTopLevel excludeIds={['t1']} value={[]} onChange={vi.fn()} />,
    );
    const second = within(screen.getByRole('group', {name: '取值标签'}));
    expect(second.getAllByRole('button').map((b) => b.textContent)).toEqual([
      '3030',
      'T40',
      '菜菜',
    ]);
  });

  it('只剩一行时不写「取值」前缀（标签自己说得出是什么）', () => {
    render(
      <TagFilter tags={TAGS} hideTopLevel value={[]} onChange={vi.fn()} />,
    );
    const second = within(screen.getByRole('group', {name: '取值标签'}));
    expect(second.queryByText('取值')).not.toBeInTheDocument();
  });

  it('候选集是二级取值时（同维度）：那个取值不出现，兄弟取值还在', () => {
    /* 候选集圈了「产品 / 3030」→ 素材已经是 3030 了，
       再把 3030 列成一个可点项点了等于没筛；
       但同维度的 T40 还得能挑（换个产品看看）。 */
    render(
      <TagFilter tags={TAGS} hideTopLevel excludeIds={['t11']} value={[]} onChange={vi.fn()} />,
    );
    const second = within(screen.getByRole('group', {name: '取值标签'}));
    expect(second.queryByRole('button', {name: '3030'})).not.toBeInTheDocument();
    expect(second.getByRole('button', {name: 'T40'})).toBeInTheDocument();
  });
});
