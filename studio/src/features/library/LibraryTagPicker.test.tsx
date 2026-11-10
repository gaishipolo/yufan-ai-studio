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
  tags: [{ id: 't1', name: '产品' }],
};

const tag = (id: string, name: string): LibraryTagStatOut => ({
  id,
  name,
  assetCount: 0,
  createdAt: '2026-11-10T00:00:00Z',
});
const TAGS = [tag('t1', '产品'), tag('t2', '人物'), tag('t3', '场景')];

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
  it('只列出还没挂上的标签，已挂的不重复出现', () => {
    renderPicker();
    expect(screen.getByRole('button', { name: '人物' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '场景' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '产品' })).not.toBeInTheDocument();
  });

  it('点选多个标签后交回**完整** tagIds，与 × 共用一次 PATCH', async () => {
    const user = userEvent.setup();
    const { onSubmit } = renderPicker();
    const add = screen.getByRole('button', { name: '添加' });
    expect(add).toBeDisabled();

    await user.click(screen.getByRole('button', { name: '人物' }));
    await user.click(screen.getByRole('button', { name: '场景' }));
    await user.click(add);
    expect(onSubmit).toHaveBeenCalledWith(ASSET, ['t1', 't2', 't3']);
  });

  it('就地新建的标签自动选中，提交时带上新 id', async () => {
    const user = userEvent.setup();
    const onCreateTag = vi.fn().mockResolvedValue('t9');
    const { onSubmit } = renderPicker({ onCreateTag });

    await user.type(screen.getByRole('textbox', { name: '新建标签名称' }), '秋冬{enter}');
    expect(onCreateTag).toHaveBeenCalledWith('秋冬');
    await user.click(screen.getByRole('button', { name: '添加' }));
    expect(onSubmit).toHaveBeenCalledWith(ASSET, ['t1', 't9']);
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
