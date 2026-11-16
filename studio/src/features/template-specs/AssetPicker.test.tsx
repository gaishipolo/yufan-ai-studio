import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { LibraryTagStatOut } from '@/shared/api/schema';
import { AssetPicker } from './AssetPicker';

/**
 * 素材选择器（封装页与模板库共用）：
 *
 * - 候选集是**槽位的标签**（作者的），范围之外的标签不出现；
 * - 使用者能在范围里按任意级别再筛（三个级别都能挑，多选是并集）；
 * - 没挑时按作者的候选集筛。
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

const TAGS: LibraryTagStatOut[] = [
  { id: 't1', name: '产品', assetCount: 7, childCount: 2, createdAt: '2026-11-09T10:01:00Z' },
  { id: 't2', name: '角色', assetCount: 3, childCount: 1, createdAt: '2026-11-09T10:02:00Z' },
  { id: 't11', name: '3030', parentId: 't1', parentName: '产品', depth: 2, assetCount: 5, childCount: 1, createdAt: '2026-11-09T10:04:00Z' },
  { id: 't31', name: 'X100', parentId: 't11', parentName: '产品 / 3030', depth: 3, assetCount: 1, childCount: 0, createdAt: '2026-11-09T10:07:00Z' },
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

describe('AssetPicker · 按任意级别标签筛选', () => {
  it('没挑标签时按槽位的候选集筛', async () => {
    renderPicker(['t1']);
    expect(selectionCalls.at(-1)?.tagIds).toEqual(['t1']);
  });

  it('范围之外的标签不出现', async () => {
    renderPicker(['t1']);
    const top = within(screen.getByRole('group', {name: '一级标签'}));
    expect(top.queryByRole('button', {name: '角色'})).not.toBeInTheDocument();
    expect(top.getByRole('button', {name: '产品'})).toBeInTheDocument();
  });

  it('按三级标签筛也能筛（任意级别都能挑）', async () => {
    const user = userEvent.setup();
    renderPicker(['t1']);
    const third = within(screen.getByRole('group', {name: '三级标签'}));
    await user.click(third.getByRole('button', {name: '产品 / 3030 / X100'}));
    expect(selectionCalls.at(-1)?.tagIds).toEqual(['t31']);
  });

  it('没圈标签的槽位也能按任意标签筛', async () => {
    const user = userEvent.setup();
    renderPicker([]);
    const top = within(screen.getByRole('group', {name: '一级标签'}));
    await user.click(top.getByRole('button', {name: '角色'}));
    expect(selectionCalls.at(-1)?.tagIds).toEqual(['t2']);
  });
});
