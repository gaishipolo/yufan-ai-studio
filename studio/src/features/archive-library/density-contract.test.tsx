import { beforeEach, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AccessProvider } from '@/shared/access/AccessProvider';
import { SourceLibrary } from './SourceLibrary';

/**
 * M2-C 契约：**同一组筛选参数在两种密度下必须走同一次查询**。
 *
 * 这条不是形式主义。这个仓付过一次代价：同一份能力做成两套实现，问题永远
 * 只在其中一套里被修，另一套长期带病——而"两套"往往就是从"紧凑视图顺手
 * 少查几个字段"开始的。所以这里断言的是**传给查询的参数对象逐键相等**，
 * 而不是"两边的卡片数量一样"：后者在 mock 固定数据下永远成立，抓不到漂移。
 *
 * 密度只允许影响渲染。
 */

const mock = vi.hoisted(() => ({
  calls: [] as Array<Record<string, unknown>>,
  items: [
    {id: 'a1', name: '第一条.mp4', category: 'source', type: 'video', sizeBytes: 10, duration: 2,
      hasAudio: true, url: '/file', createdAt: '2026-11-06T00:00:00Z', stateVersion: 1,
      editable: true, shared: false, downloadAllowed: true},
    {id: 'a2', name: '第二条.png', category: 'scene', type: 'image', sizeBytes: 20, duration: 0,
      hasAudio: false, url: '/file2', createdAt: '2026-11-05T00:00:00Z', stateVersion: 1,
      editable: true, shared: false, downloadAllowed: true},
  ],
}));

vi.mock('@/entities/asset/api/archive.queries', () => ({
  useArchiveAssets: (_category: string, options: Record<string, unknown>) => {
    mock.calls.push(options);
    return {isPending: false, isFetching: false, error: null, refetch: vi.fn(),
      data: {total: mock.items.length, items: mock.items}};
  },
  useArchiveChange: () => ({mutate: vi.fn(), isPending: false, isSuccess: false, error: null}),
  useArchivedMedia: () => ({data: undefined, error: null}),
  useArchiveDownload: () => ({isPending: false, mutate: vi.fn()}),
}));
vi.mock('./model/use-upload-batch', () => ({
  useUploadBatchStore: (selector: (state: unknown) => unknown) => selector({batch: null, start: vi.fn()}),
}));

const FILTERS = {
  scope: 'team' as const,
  teamId: 't_content',
  categoryId: 'cat-2',
  mediaType: 'video',
  tagIds: ['style-0', 'shot-1'],
};

function renderWith(density: 'full' | 'compact') {
  return render(
    <MemoryRouter>
      <AccessProvider subject={{id: 'A', name: '测试', roles: [], permissions: ['asset:read']}}>
        <SourceLibrary {...FILTERS} density={density} toolbar="none" />
      </AccessProvider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  mock.calls.length = 0;
});

it('两种密度下发起的查询参数完全相同', () => {
  renderWith('full');
  renderWith('compact');
  expect(mock.calls).toHaveLength(2);
  const [card, list] = mock.calls;
  // 逐键相等：`toEqual` 就够，但要排除只影响渲染的项——这里本来就没有，
  // 所以任何新增的多余字段都会让这条红，正是想要的行为
  expect(list).toEqual(card);
  // 反向确认：筛选真的传下去了，否则"两者相同"可能只是"两者都是空"
  expect(card).toMatchObject({
    scope: 'team',
    teamId: 't_content',
    categoryId: 'cat-2',
    mediaType: 'video',
    tagIds: ['style-0', 'shot-1'],
  });
});

it('两种密度渲染出同样多的条目', () => {
  const {unmount} = renderWith('full');
  expect(screen.getAllByText(/第[一二]条/)).toHaveLength(2);
  unmount();
  renderWith('compact');
  expect(screen.getAllByText(/第[一二]条/)).toHaveLength(2);
});

it('团队库缺 teamId 时不猜，直接把空串交出去由服务端拒绝', () => {
  // 前端不在这里"二选一挑个团队"：那会让"忘了选库"变成"看到了别人的库"，
  // 而界面上没有任何提示。
  render(
    <MemoryRouter>
      <AccessProvider subject={{id: 'A', name: '测试', roles: [], permissions: ['asset:read']}}>
        <SourceLibrary scope="team" toolbar="none" />
      </AccessProvider>
    </MemoryRouter>,
  );
  expect(mock.calls[0].scope).toBe('team');
  expect(mock.calls[0].teamId).toBeUndefined();
});
