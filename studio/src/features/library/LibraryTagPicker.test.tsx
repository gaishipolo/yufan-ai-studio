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

/** 两级词表：「产品」是维度，下面挂 3030 / T35；「人物」「场景」是孤立一级（叶子）。
 *  有下级的维度只做分组，不能打在素材上。 */
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
  it('只列叶子：有下级的维度不能打；已挂的不重复出现', () => {
    renderPicker();
    expect(screen.queryByRole('button', { name: '产品' })).not.toBeInTheDocument();
    // 已挂在素材上的「产品 / 3030」不重复出现
    expect(screen.queryByRole('button', { name: '产品 / 3030' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '产品 / T35' })).toBeInTheDocument();
    // 孤立一级本身就是可打标的词
    expect(screen.getByRole('button', { name: '人物' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '场景' })).toBeInTheDocument();
  });

  it('分组标题用父标签的整条链，取值能看出挂在哪个维度下', () => {
    renderPicker();
    expect(screen.getByText('产品')).toBeInTheDocument();
    expect(screen.getByText('未分组')).toBeInTheDocument();
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

  it('**没有就地新建入口**：标签词表统一在「标签管理」里建', () => {
    /* 守卫：就地新建只能建一级（挂不上维度），建出来还得再去标签管理里挪一次。
       取消这个能力后，这个弹窗里不该再有新建输入框与上级下拉。 */
    renderPicker();
    expect(screen.queryByRole('textbox', { name: '新建标签名称' })).not.toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: '新标签所属上级' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '新建' })).not.toBeInTheDocument();
  });

  it('词表里的标签都已添加时，只给一句说明（不再引导去下方新建）', () => {
    renderPicker({ tags: [tag('t11', '3030', { parentId: 't1', parentName: '产品', depth: 2 })] });
    expect(screen.getByText('词表里的标签都已添加')).toBeInTheDocument();
    expect(screen.queryByText(/新建一个/)).not.toBeInTheDocument();
  });

  it('提交中禁用按钮，避免连点发出互相覆盖的 PATCH', () => {
    renderPicker({ busy: true });
    expect(screen.getByRole('button', { name: '添加中…' })).toBeDisabled();
  });
});
