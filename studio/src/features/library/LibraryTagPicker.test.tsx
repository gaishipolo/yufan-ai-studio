import type { ComponentProps } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { LibraryAssetOut, LibraryTagStatOut } from '@/shared/api/schema';
import { LibraryTagPicker } from './LibraryTagPicker';

const ASSET: LibraryAssetOut = {
  id: 'a1',
  name: '3030粉产品细节图.png',
  kind: 'image',
  origin: 'upload',
  ownerId: 'u1',
  visibility: 'personal',
  state: 'active',
  createdAt: '2026-11-10T02:00:00Z',
  tags: [{ id: 't11', name: '3030', parentId: 't1', parentName: '产品', depth: 2 }],
};

const tag = (id: string, name: string, extra: Partial<LibraryTagStatOut> = {}): LibraryTagStatOut => ({
  id,
  name,
  assetCount: 0,
  childCount: 0,
  createdAt: '2026-11-10T00:00:00Z',
  ...extra,
});

/** 「产品」旗下有 3030 / T35，所以它只是分组；「人物」「场景」是孤立一级（叶子） */
const TAGS = [
  tag('t1', '产品', { childCount: 2 }),
  tag('t2', '人物'),
  tag('t3', '场景'),
  tag('t11', '3030', { parentId: 't1', parentName: '产品', depth: 2 }),
  tag('t12', 'T35', { parentId: 't1', parentName: '产品', depth: 2 }),
];

function renderPicker(overrides: Partial<ComponentProps<typeof LibraryTagPicker>> = {}) {
  const onSubmit = vi.fn();
  const onClose = vi.fn();
  render(
    <LibraryTagPicker
      asset={ASSET}
      tags={TAGS}
      onSubmit={onSubmit}
      onClose={onClose}
      {...overrides}
    />,
  );
  return { onSubmit, onClose };
}

describe('LibraryTagPicker · 卡片上的 +', () => {
  it('只列叶子：有二级的一级是分组，不能打；已挂的不重复出现', () => {
    renderPicker();
    expect(screen.queryByRole('button', { name: '产品' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '产品 / 3030' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '产品 / T35' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '人物' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '场景' })).toBeInTheDocument();
    expect(screen.getByText('产品')).toBeInTheDocument(); // 分组标题
  });

  it('点选多个标签后交回**完整** tagIds，与 × 共用一次 PATCH', async () => {
    const user = userEvent.setup();
    const { onSubmit } = renderPicker();
    const add = screen.getByRole('button', { name: '添加' });
    expect(add).toBeDisabled();

    await user.click(screen.getByRole('button', { name: '人物' }));
    await user.click(screen.getByRole('button', { name: '产品 / T35' }));
    await user.click(add);
    expect(onSubmit).toHaveBeenCalledWith(ASSET, ['t11', 't2', 't12']);
  });

  it('就地新建一级标签自动选中，提交时带上新 id', async () => {
    const user = userEvent.setup();
    const onCreateTag = vi.fn().mockResolvedValue('t9');
    const { onSubmit } = renderPicker({ onCreateTag });

    await user.type(screen.getByRole('textbox', { name: '新建标签名称' }), '秋冬{enter}');
    expect(onCreateTag).toHaveBeenCalledWith('秋冬', undefined);
    await user.click(screen.getByRole('button', { name: '添加' }));
    expect(onSubmit).toHaveBeenCalledWith(ASSET, ['t11', 't9']);
  });

  it('新建时选了父级，就带着 parentId 建成二级', async () => {
    const user = userEvent.setup();
    const onCreateTag = vi.fn().mockResolvedValue('t9');
    renderPicker({ onCreateTag });

    await user.selectOptions(
      screen.getByRole('combobox', { name: '新标签所属一级' }),
      't1',
    );
    await user.type(screen.getByRole('textbox', { name: '新建标签名称' }), 'T40{enter}');
    expect(onCreateTag).toHaveBeenCalledWith('T40', 't1');
    expect(screen.getByRole('button', { name: '产品 / T40' })).toBeInTheDocument();
  });

  it('没有 library:create 时不摆一个必定失败的新建输入框', () => {
    renderPicker();
    expect(screen.queryByRole('textbox', { name: '新建标签名称' })).not.toBeInTheDocument();
  });

  it('提交中禁用按钮，避免连点发出互相覆盖的 PATCH', () => {
    renderPicker({ busy: true });
    expect(screen.getByRole('button', { name: '添加中…' })).toBeDisabled();
  });
});
