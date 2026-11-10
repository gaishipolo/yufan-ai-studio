import type { ComponentProps } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { LibraryAssetOut } from '@/shared/api/schema';
import { LibraryCard } from './LibraryCard';

const ASSET: LibraryAssetOut = {
  id: 'a1',
  name: '3030粉产品细节图.png',
  kind: 'image',
  origin: 'upload',
  ownerId: 'u1',
  ownerName: '老大',
  visibility: 'personal',
  state: 'active',
  sizeBytes: 1_572_864,
  createdAt: '2026-11-10T02:00:00Z',
  tags: [
    { id: 't1', name: '3030' },
    { id: 't2', name: '产品' },
    { id: 't3', name: '细节图' },
  ],
};

const VIEWER = { id: 'u1', canDeleteAny: false };

function renderCard(overrides: Partial<ComponentProps<typeof LibraryCard>> = {}) {
  const onRemoveTag = vi.fn();
  const onAddTag = vi.fn();
  const props = {
    asset: ASSET,
    view: 'personal' as const,
    selected: false,
    viewer: VIEWER,
    canUpdate: true,
    onToggle: vi.fn(),
    onAction: vi.fn(),
    onRemoveTag,
    onAddTag,
    ...overrides,
  };
  render(<LibraryCard {...props} />);
  return { onRemoveTag, onAddTag };
}

describe('LibraryCard · 标签', () => {
  it('全部标签都显示出来，不折叠成「两枚 +N」', () => {
    renderCard();
    for (const name of ['3030', '产品', '细节图']) {
      expect(screen.getByText(name)).toBeInTheDocument();
    }
    expect(screen.queryByText('+1')).not.toBeInTheDocument();
  });

  it('有编辑权限时每个标签带 ×，点它把 asset 与 tagId 交给上层', async () => {
    const user = userEvent.setup();
    const { onRemoveTag } = renderCard();

    expect(screen.getAllByRole('button', { name: /^移除标签/ })).toHaveLength(3);
    await user.click(screen.getByRole('button', { name: '移除标签 产品' }));
    expect(onRemoveTag).toHaveBeenCalledWith(ASSET, 't2');
  });

  it('标签行末尾有 +，点它把 asset 交给上层打开选择器', async () => {
    const user = userEvent.setup();
    const { onAddTag } = renderCard();

    const add = screen.getByRole('button', { name: /添加标签/ });
    await user.click(add);
    expect(onAddTag).toHaveBeenCalledWith(ASSET);
  });

  it('一个标签都没有时仍然留着 +，否则第一条素材无从打标', () => {
    renderCard({ asset: { ...ASSET, tags: [] } });
    expect(screen.getByRole('button', { name: /添加标签/ })).toBeInTheDocument();
  });

  it('没有更新权限时只读展示，不给出注定 403 的 × 和 +', () => {
    renderCard({ canUpdate: false });
    expect(screen.getByText('产品')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^移除标签/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /添加标签/ })).not.toBeInTheDocument();
  });

  it('不是本人、也不是管理员时同样不给 × 和 +', () => {
    renderCard({ viewer: { id: 'someone-else', canDeleteAny: false } });
    expect(screen.queryByRole('button', { name: /^移除标签/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /添加标签/ })).not.toBeInTheDocument();
  });
});
