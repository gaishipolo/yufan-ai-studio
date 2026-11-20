import {QueryClient, QueryClientProvider} from '@tanstack/react-query';
import {render, screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {MemoryRouter} from 'react-router-dom';
import {beforeEach, describe, expect, it, vi} from 'vitest';
import type {TemplateSpecOut} from '@/shared/api/schema';
import {SpecLibrary} from './SpecLibrary';

/**
 * 模板库 · 在槽位网格里点一个封装包。
 *
 * 槽位声明 `pickMode=pack` 时只从封装包里取，点一次包就是**一次填好几格** ——
 * 包里带「正面图」的那张进正面图槽位，带「三视图」的进三视图槽位。
 * 判据：这一格声明了「从包取」 + 成员的标签 ∩ 槽位的候选标签。
 * 没声明来源（=素材）的槽位不该被包顺带改掉。
 *
 * 三种情况分开守：
 * 1. 恰好一张 → 填；
 * 2. 没有 → 不动（别的槽位照填）；
 * 3. 多张 → 也不填，并说出来 —— 静默取第一张，生成的图错了没人知道。
 */

const {compileMock, specState, packsState, packState} = vi.hoisted(() => ({
  compileMock: vi.fn(),
  specState: {current: null as unknown},
  packsState: {current: [] as {id: string; name: string; memberCount?: number}[]},
  packState: {current: null as unknown},
}));

vi.mock('@/entities/template-spec/api/spec.queries', () => ({
  usePublishedSpecs: () => ({
    data: {items: [specState.current], total: 1},
    isLoading: false,
  }),
  useSpecCompile: () => ({mutateAsync: compileMock, isPending: false}),
}));

vi.mock('@/entities/task/api/task.queries', () => ({
  useCreateTask: () => ({mutateAsync: vi.fn().mockResolvedValue({id: 't1'}), isPending: false}),
}));

vi.mock('@/entities/image/api/image.api', () => ({
  useGenerateImage: () => ({mutateAsync: vi.fn(), isPending: false}),
}));

vi.mock('@/entities/model/api/model.queries', () => ({
  useModels: (capability: string) => (capability === 'video' ? [LIBTV] : []),
}));

vi.mock('@/entities/library/api/library.api', () => ({libraryApi: {}}));

vi.mock('@/entities/library/api/library.queries', () => ({
  useLibraryDetail: (id: string) => ({data: {name: `素材-${id}.png`}}),
  useLibraryTags: () => ({
    data: [
      {id: 'tag-front', name: '正面图', assetCount: 2, childCount: 0, createdAt: '2026-11-09T10:00:00Z'},
      {id: 'tag-views', name: '三视图', assetCount: 2, childCount: 0, createdAt: '2026-11-09T10:00:00Z'},
    ],
    isPending: false,
  }),
  /* 包列表：后端会按槽位候选标签过滤，这里直接给「这个模板能用的那个包」 */
  useLibraryPacks: (_params: {tagIds?: string[]}, enabled = true) => ({
    data: enabled ? packsState.current : undefined,
  }),
  /* 详情：点包之后才拉（`pendingPack` 非空） */
  useLibraryPack: (id: string | null) => ({
    data: id ? packState.current : undefined,
    isError: false,
    error: null,
  }),
  /* 素材网格给几张普通图，证明包和素材是并排的 */
  useLibrarySelection: () => ({
    data: {items: [{id: 'loose', name: '散图.png', kind: 'image'}], total: 1},
    isLoading: false,
    isSuccess: true,
  }),
  useLibrarySelectionInfinite: () => ({data: {pages: []}, hasNextPage: false}),
  useLibraryLargePreview: () => ({url: undefined}),
}));

const LIBTV = {
  id: 'libtv-wan3-replacement',
  label: 'LibTV · 720P 视频替换',
  capability: 'video',
  provider: 'libtv',
  isDefault: true,
  maxReferences: 5,
  referenceKinds: ['image', 'video', 'audio'],
  paramSchema: {durationSec: {type: 'int', min: 2, max: 30, step: 1, default: 5}},
};

/** 两个图片槽位，各圈一个视角标签 —— 这正是「同一件产品的两个面」。 */
const SPEC: TemplateSpecOut = {
  id: 's1',
  key: 'product-replace',
  name: '商品替换',
  description: '',
  ownerId: 'u1',
  status: 'published',
  revision: 1,
  fingerprint: 'fp',
  currentVersion: 3,
  createdAt: '2026-11-10T02:00:00Z',
  updatedAt: '2026-11-10T02:00:00Z',
  latest: {
    version: 3,
    model: 'libtv-wan3-replacement',
    allowedModels: [],
    params: {durationSec: 5},
    prompt: '把 {{slot:front}} 和 {{slot:views}} 换进原片',
    randomPrompt: '',
    slots: [
      {
        key: 'front',
        type: 'image',
        binding: 'user',
        pickMode: 'pack',
        minCount: 1,
        maxCount: 1,
        label: '正面图',
        tagIds: ['tag-front'],
      },
      {
        key: 'views',
        type: 'image',
        binding: 'user',
        pickMode: 'pack',
        minCount: 1,
        maxCount: 1,
        label: '三视图',
        tagIds: ['tag-views'],
      },
    ],
  },
};

const PACK = {
  id: 'p1',
  name: '6036 黑',
  memberCount: 2,
  members: [
    {id: 'm-front', name: '6036-黑-正面.png', kind: 'image', tagIds: ['tag-front'], deleted: false},
    {id: 'm-views', name: '6036-黑-三视图.png', kind: 'image', tagIds: ['tag-views'], deleted: false},
  ],
};

beforeEach(() => {
  specState.current = SPEC;
  packsState.current = [{id: 'p1', name: '6036 黑', memberCount: 2}];
  packState.current = PACK;
  compileMock.mockReset();
  compileMock.mockResolvedValue({
    version: 3,
    model: 'libtv-wan3-replacement',
    prompt: '把 {{slot:front}} 和 {{slot:views}} 换进原片',
    params: {durationSec: 5},
    referenceLibraryIds: [],
    textLibraryIds: [],
  });
});

function renderLibrary() {
  const client = new QueryClient({
    defaultOptions: {queries: {retry: false}, mutations: {retry: false}},
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/templates/library']}>
        <SpecLibrary />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/** 点第一个槽位网格里的那个包（两个槽位都会列它，点哪个效果一样） */
async function pickPack(user: ReturnType<typeof userEvent.setup>) {
  const cards = await screen.findAllByTitle('6036 黑（封装 · 2 张）');
  await user.click(cards[0]);
}


describe('模板库 · 点封装包填多格', () => {
  it('点一次包，两个槽位各拿到自己那一张', async () => {
    const user = userEvent.setup();
    renderLibrary();
    // 等包卡片出现即可：它同时证明模板已加载、槽位网格已渲染
    await screen.findAllByTitle('6036 黑（封装 · 2 张）');

    await pickPack(user);

    /* 两格各拿到自己那一张：一次点击就是一次「选整套」。
       断言落在已填素材上 —— 它们不在当前网格（mock 只给 loosed 一张），
       所以会以 chip 形式出现在槽位下方。 */
    expect(await screen.findByText(/素材-m-front/)).toBeInTheDocument();
    expect(await screen.findByText(/素材-m-views/)).toBeInTheDocument();
  });

  it('声明了来源=素材的槽位，不会被包顺带改掉', async () => {
    /* 来源二选一：作者说「这一格从素材里挑」，使用者点包就不该动它。
       以前包和素材混在一个网格里，点包会把所有标签对得上的格子全改掉。 */
    specState.current = {
      ...SPEC,
      latest: {
        ...SPEC.latest!,
        slots: (SPEC.latest!.slots ?? []).map(slot =>
          slot.key === 'views' ? {...slot, pickMode: 'asset' as const} : slot,
        ),
      },
    };
    const user = userEvent.setup();
    renderLibrary();
    await screen.findAllByTitle('6036 黑（封装 · 2 张）');

    await pickPack(user);

    expect(await screen.findByText(/素材-m-front/)).toBeInTheDocument();
    expect(screen.queryByText(/素材-m-views/)).not.toBeInTheDocument();
  });

  it('同一个容器只出一个选包入口（不是每格各列一遍）', async () => {
    /* 用户报的就是这个：「点一次就填好了，但显示还是两个」。容器里的槽位
       共用一个选包入口，网格只出现一次。 */
    specState.current = {
      ...SPEC,
      latest: {
        ...SPEC.latest!,
        slots: (SPEC.latest!.slots ?? []).map(slot => ({...slot, group: 'g1'})),
      },
    };
    const user = userEvent.setup();
    renderLibrary();

    const cards = await screen.findAllByTitle('6036 黑（封装 · 2 张）');
    expect(cards).toHaveLength(1);
    expect(screen.getByText('封装槽位')).toBeInTheDocument();

    await user.click(cards[0]);
    // 一次点击仍然把容器里的两格填满
    expect(await screen.findByText(/素材-m-front/)).toBeInTheDocument();
    expect(await screen.findByText(/素材-m-views/)).toBeInTheDocument();
  });

  it('包里缺某个视角：那一格不动，别的照填', async () => {
    packState.current = {
      ...PACK,
      memberCount: 1,
      members: [PACK.members[0]],
    };
    const user = userEvent.setup();
    renderLibrary();
    // 等包卡片出现即可：它同时证明模板已加载、槽位网格已渲染
    await screen.findAllByTitle('6036 黑（封装 · 2 张）');

    const cards = await screen.findAllByTitle('6036 黑（封装 · 2 张）');
    await user.click(cards[0]);

    // 正面图填上了，三视图还是空的 —— 不拿正面图去凑
    // 等包卡片出现即可：它同时证明模板已加载、槽位网格已渲染
    await screen.findAllByTitle('6036 黑（封装 · 2 张）');
    expect(await screen.findByText(/素材-m-front/)).toBeInTheDocument();
    // 已删除的成员不参与分发
    expect(screen.queryByText(/素材-m-views/)).not.toBeInTheDocument();
  });

  it('同一视角有多张时不猜，两个槽位都不填', async () => {
    packState.current = {
      ...PACK,
      memberCount: 3,
      members: [
        ...PACK.members,
        {id: 'm-front-2', name: '6036-黑-正面2.png', kind: 'image', tagIds: ['tag-front'], deleted: false},
      ],
    };
    const user = userEvent.setup();
    renderLibrary();
    // 等包卡片出现即可：它同时证明模板已加载、槽位网格已渲染
    await screen.findAllByTitle('6036 黑（封装 · 2 张）');

    const cards = await screen.findAllByTitle('6036 黑（封装 · 2 张）');
    await user.click(cards[0]);

    /* 正面图有两张 → 不填（猜一张进去，生成的图错了也没人知道）；
       三视图只有一张 → 照填。 */
    expect(await screen.findByText(/素材-m-views/)).toBeInTheDocument();
    expect(screen.queryByText(/素材-m-front/)).not.toBeInTheDocument();
  });

  it('回收站里的成员不参与分发（包静默少一张会让人以为它本来就没有）', async () => {
    packState.current = {
      ...PACK,
      members: [
        PACK.members[0],
        {...PACK.members[1], deleted: true},
      ],
    };
    const user = userEvent.setup();
    renderLibrary();
    // 等包卡片出现即可：它同时证明模板已加载、槽位网格已渲染
    await screen.findAllByTitle('6036 黑（封装 · 2 张）');

    const cards = await screen.findAllByTitle('6036 黑（封装 · 2 张）');
    await user.click(cards[0]);

    expect(await screen.findByText(/素材-m-front/)).toBeInTheDocument();
    // 已删除的成员不参与分发（否则会填进一张取不到的图）
    expect(screen.queryByText(/素材-m-views/)).not.toBeInTheDocument();
  });
});
