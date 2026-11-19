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
  const view = render(<LibraryCard {...props} />);
  return { onRemoveTag, onAddTag, container: view.container };
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

const TEXT_ASSET: LibraryAssetOut = {
  ...ASSET,
  id: 't1',
  name: '分镜脚本.md',
  kind: 'text',
  coverUrl: null,
  previewUrl: null,
  textExcerpt: '第一段：雨夜街头，霓虹灯牌。\n第二段：镜头缓缓推近。',
  textVersion: 3,
};

describe('LibraryCard · 文本资产', () => {
  it('不渲染缩略图（不请求 /thumbnail），改用 textExcerpt 画文字摘要', () => {
    const { container } = renderCard({ asset: TEXT_ASSET });
    // 其它类型会交给 CoverImage 画 <img>；文本卡一张都不该有
    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByText(/第一段：雨夜街头/)).toBeInTheDocument();
    // 只有右上角那枚「文本」徽标；底部深色类型条是给封面图用的白底文字卡不要
    expect(screen.getAllByText('文本')).toHaveLength(1);
    expect(container.querySelector('.bg-gradient-to-t')).toBeNull();
  });

  it('没有摘录时显示「（空文本）」，仍不渲染破图', () => {
    const { container } = renderCard({ asset: { ...TEXT_ASSET, textExcerpt: null } });
    expect(screen.getByText('（空文本）')).toBeInTheDocument();
    expect(container.querySelector('img')).toBeNull();
  });
});

/** 封装：没有自己的字节，只有成员。 */
const PACK_ASSET: LibraryAssetOut = {
  id: 'p1',
  name: '6036 黑',
  kind: 'pack',
  origin: 'upload',
  ownerId: 'u1',
  visibility: 'personal',
  state: 'active',
  sizeBytes: 0,
  memberCount: 3,
  createdAt: '2026-11-19T02:00:00Z',
  tags: [{ id: 't9', name: '6036' }],
};

describe('LibraryCard · 封装', () => {
  it('「多大」显示成几张素材，而不是 0 B', () => {
    renderCard({ asset: PACK_ASSET });
    expect(screen.getByText('3 张素材')).toBeInTheDocument();
    // 0 字节是「它自己没有东西」，不是「这个包是空的」—— 显示 0 B 会读成后者
    expect(screen.queryByText('0 B')).not.toBeInTheDocument();
  });

  it('底部类型条写「封装」', () => {
    renderCard({ asset: PACK_ASSET });
    expect(screen.getByText('封装')).toBeInTheDocument();
  });

  it('不给下载按钮：包没有原文件，点了必然失败', () => {
    renderCard({ asset: PACK_ASSET });
    expect(screen.queryByRole('button', { name: '下载原文件' })).not.toBeInTheDocument();
    // 同一行里别的按钮还在（共享、删除），不是整行没渲染
    expect(screen.getByRole('button', { name: '共享到…' })).toBeInTheDocument();
  });

  it('普通素材仍有下载按钮（上面那条不是因为整行被藏掉）', () => {
    renderCard();
    expect(screen.getByRole('button', { name: '下载原文件' })).toBeInTheDocument();
  });
});
