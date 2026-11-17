import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { LibraryTagStatOut } from '@/shared/api/schema';
import { LibraryTagManager } from './LibraryTagManager';

/**
 * 标签词表管理：**唯一**能新建标签的地方。
 *
 * 打标签的各处（卡片 + 号、上传、批量、详情）都已经取消就地新建 ——
 * 那里的新建只能建一级（挂不上维度），建出来还得再来这里挪一次层级。
 * 所以这里是词表结构的唯一入口，守卫要钉住它还能建一级与二级。
 */

const { createCalls } = vi.hoisted(() => ({
  createCalls: [] as { name: string; parentId?: string }[],
}));

vi.mock('@/entities/library/api/library.queries', () => ({
  useCreateTag: () => ({
    mutateAsync: (payload: { name: string; parentId?: string }) => {
      createCalls.push(payload);
      return Promise.resolve({ id: 'new', name: payload.name });
    },
    isPending: false,
  }),
  useRenameTag: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useMoveTag: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteTag: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

/** 两级词表：「产品」是维度，下面挂 6036 / T35；「音乐」是孤立一级（叶子）。 */
const TAGS: LibraryTagStatOut[] = [
  { id: 't1', name: '产品', assetCount: 3, childCount: 2, createdAt: '2026-11-09T10:01:00Z' },
  { id: 't11', name: '6036', parentId: 't1', parentName: '产品', depth: 2, assetCount: 1, childCount: 0, createdAt: '2026-11-09T10:02:00Z' },
  { id: 't12', name: 'T35', parentId: 't1', parentName: '产品', depth: 2, assetCount: 1, childCount: 0, createdAt: '2026-11-09T10:03:00Z' },
  { id: 't2', name: '音乐', assetCount: 0, childCount: 0, createdAt: '2026-11-09T10:04:00Z' },
];

beforeEach(() => {
  createCalls.length = 0;
});

function renderManager(tags: LibraryTagStatOut[] = TAGS) {
  render(<LibraryTagManager tags={tags} onClose={vi.fn()} />);
}

describe('LibraryTagManager · 词表结构的唯一入口', () => {
  it('能新建一级标签（不选上级）', async () => {
    const user = userEvent.setup();
    renderManager();
    await user.type(screen.getByRole('textbox', { name: '新标签名称' }), '场景{enter}');
    expect(createCalls).toEqual([{ name: '场景', parentId: undefined }]);
  });

  it('能新建二级标签（选了上级）', async () => {
    const user = userEvent.setup();
    renderManager();
    await user.selectOptions(screen.getByRole('combobox', { name: '新标签所属上级' }), 't1');
    await user.type(screen.getByRole('textbox', { name: '新标签名称' }), 'T40{enter}');
    expect(createCalls).toEqual([{ name: 'T40', parentId: 't1' }]);
  });

  it('上级下拉里只有一级维度：取值不能当上级（那会造出第三层）', () => {
    renderManager();
    const select = screen.getByRole('combobox', { name: '新标签所属上级' });
    expect(select.querySelector('option[value="t11"]')).not.toBeInTheDocument();
    expect(select.querySelector('option[value="t1"]')).not.toBeNull();
  });

  it('列表按「一级维度 → 二级取值」原序排下来', () => {
    renderManager();
    const names = screen
      .getAllByRole('textbox')
      .map((input) => input.getAttribute('aria-label'))
      .filter((label): label is string => Boolean(label?.endsWith(' 的名称')));
    expect(names).toEqual(['产品 的名称', '6036 的名称', 'T35 的名称', '音乐 的名称']);
  });
});
