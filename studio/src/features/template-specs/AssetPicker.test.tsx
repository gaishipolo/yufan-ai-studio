import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { LibraryTagStatOut } from '@/shared/api/schema';
import { AssetPicker } from './AssetPicker';

/**
 * 素材选择器（封装页与模板库共用）：
 *
 * - 候选集是**槽位的标签**（作者的）—— 那圈定了素材范围；
 * - 界面上只列**这批素材身上实际有**的标签，用户点了就在范围里再筛；
 * - 使用者的挑按维度**替换**候选集里那一维，不是叠加。
 *
 * 断言落在「发给后端的 tagIds」上 —— 筛选语义真正发生的地方。
 */

const { selectionCalls, tagsData } = vi.hoisted(() => ({
  selectionCalls: [] as { kind?: string; tagIds?: string[] }[],
  tagsData: { current: [] as LibraryTagStatOut[] },
}));

vi.mock('@/entities/library/api/library.queries', () => ({
  useLibraryTags: () => ({ data: tagsData.current }),
}));

vi.mock('@/entities/library/api/library.api', () => ({
  libraryApi: {
    selection: (params: { kind?: string; tagIds?: string[] }) => {
      selectionCalls.push(params);
      return Promise.resolve({ items: [], total: 0, page: 1, pageSize: 24 });
    },
  },
}));

/** 两级：一级是维度（产品 / 角色），二级是取值（3030 / 菜菜）。 */
const TAGS: LibraryTagStatOut[] = [
  { id: 't1', name: '产品', assetCount: 7, childCount: 2, createdAt: '2026-11-09T10:01:00Z' },
  { id: 't2', name: '角色', assetCount: 3, childCount: 1, createdAt: '2026-11-09T10:02:00Z' },
  { id: 't11', name: '3030', parentId: 't1', parentName: '产品', depth: 2, assetCount: 5, childCount: 0, createdAt: '2026-11-09T10:04:00Z' },
  { id: 't21', name: '菜菜', parentId: 't2', parentName: '角色', depth: 2, assetCount: 3, childCount: 0, createdAt: '2026-11-09T10:06:00Z' },
];

beforeEach(() => {
  selectionCalls.length = 0;
  tagsData.current = TAGS;
});

function renderPicker(tagIds: string[]) {
  const client = new QueryClient({defaultOptions: {queries: {retry: false}}});
  return render(
    <QueryClientProvider client={client}>
      <AssetPicker
        open
        kind="image"
        tagIds={tagIds}
        selected={[]}
        onClose={vi.fn()}
        onToggle={vi.fn()}
      />
    </QueryClientProvider>,
  );
}

describe('AssetPicker · 候选集里再筛', () => {
  it('没挑标签时按槽位的候选集筛', async () => {
    renderPicker(['t1']);
    expect(selectionCalls.at(-1)?.tagIds).toEqual(['t1']);
  });

  it('只列这批素材身上实际有的标签（计数为 0 的不出现）', async () => {
    /* 词表里「角色」没有素材 → 不该出现，用户点了只会得到空列表。 */
    tagsData.current = TAGS.map((tag) =>
      tag.id === 't2' ? { ...tag, assetCount: 0 } : tag,
    );
    renderPicker(['t1']);
    const top = within(screen.getByRole('group', {name: '一级标签'}));
    expect(top.queryByRole('button', {name: '角色'})).not.toBeInTheDocument();
    expect(top.getByRole('button', {name: '产品'})).toBeInTheDocument();
  });

  it('同一维度：使用者的挑替换候选集里那一维', async () => {
    const user = userEvent.setup();
    renderPicker(['t1']);
    const second = within(screen.getByRole('group', {name: '二级标签'}));
    await user.click(second.getByRole('button', {name: '3030'}));
    expect(selectionCalls.at(-1)?.tagIds).toEqual(['t11']);
  });

  it('跨维度：候选集那一维保留，使用者挑的加进来', async () => {
    const user = userEvent.setup();
    renderPicker(['t1']);
    const second = within(screen.getByRole('group', {name: '二级标签'}));
    await user.click(second.getByRole('button', {name: '菜菜'}));
    expect(selectionCalls.at(-1)?.tagIds).toEqual(['t1', 't21']);
  });

  it('没圈标签的槽位也能按任意标签筛', async () => {
    const user = userEvent.setup();
    renderPicker([]);
    const top = within(screen.getByRole('group', {name: '一级标签'}));
    await user.click(top.getByRole('button', {name: '角色'}));
    expect(selectionCalls.at(-1)?.tagIds).toEqual(['t2']);
  });
});
