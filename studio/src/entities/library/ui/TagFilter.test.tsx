import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { LibraryTagStatOut } from '@/shared/api/schema';
import { TagFilter } from './TagFilter';

const TAGS: LibraryTagStatOut[] = [
  { id: 't1', name: '产品', assetCount: 7, childCount: 2, createdAt: '2026-11-09T10:01:00Z' },
  { id: 't2', name: '角色', assetCount: 3, childCount: 1, createdAt: '2026-11-09T10:02:00Z' },
  { id: 't11', name: '3030', parentId: 't1', parentName: '产品', depth: 2, assetCount: 5, childCount: 1, createdAt: '2026-11-09T10:04:00Z' },
  { id: 't21', name: '菜菜', parentId: 't2', parentName: '角色', depth: 2, assetCount: 3, childCount: 0, createdAt: '2026-11-09T10:06:00Z' },
  { id: 't31', name: 'X100', parentId: 't11', parentName: '产品 / 3030', depth: 3, assetCount: 1, childCount: 0, createdAt: '2026-11-09T10:07:00Z' },
];

describe('TagFilter · 三级多选标签筛选', () => {
  it('三行分别列出一级 / 二级 / 三级', () => {
    render(<TagFilter tags={TAGS} value={[]} onChange={vi.fn()} />);
    const top = within(screen.getByRole('group', {name: '一级标签'}));
    const second = within(screen.getByRole('group', {name: '二级标签'}));
    const third = within(screen.getByRole('group', {name: '三级标签'}));
    expect(top.getAllByRole('button').map((b) => b.textContent)).toEqual([
      '产品',
      '角色',
    ]);
    expect(second.getAllByRole('button').map((b) => b.textContent)).toEqual([
      '产品 / 3030',
      '角色 / 菜菜',
    ]);
    expect(third.getAllByRole('button').map((b) => b.textContent)).toEqual([
      '产品 / 3030 / X100',
    ]);
  });

  it('范围之外的标签不出现：作者与使用者看到的必须是同一批素材', () => {
    render(<TagFilter tags={TAGS} scope={['t1']} value={[]} onChange={vi.fn()} />);
    const top = within(screen.getByRole('group', {name: '一级标签'}));
    expect(top.queryByRole('button', {name: '角色'})).not.toBeInTheDocument();
    const second = within(screen.getByRole('group', {name: '二级标签'}));
    expect(second.queryByRole('button', {name: '角色 / 菜菜'})).not.toBeInTheDocument();
    expect(second.getByRole('button', {name: '产品 / 3030'})).toBeInTheDocument();
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

  it('挑了别的标签时父标签不会被踢掉（三个级别可任意组合）', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<TagFilter tags={TAGS} value={['t1']} onChange={onChange} />);
    const second = within(screen.getByRole('group', {name: '二级标签'}));
    await user.click(second.getByRole('button', {name: '产品 / 3030'}));
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
