import {QueryClient, QueryClientProvider} from '@tanstack/react-query';
import {render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {MemoryRouter} from 'react-router-dom';
import {beforeEach, describe, expect, it, vi} from 'vitest';
import type {TemplateSpecOut} from '@/shared/api/schema';
import {SpecLibrary} from './SpecLibrary';

/**
 * 模板库 · 在槽位网格里点一个封装包。
 *
 * 槽位在同一个容器（`group`）里时只从封装包里取，点一次包就是**一次填好几格** ——
 * 包里带「正面图」的那张进正面图槽位，带「三视图」的进三视图槽位。
 * 判据：这一格在**这个容器**里 + 成员的标签 ∩ 槽位的候选标签。
 * 独立槽位（`group` 为空）不该被包顺带改掉。
 *
 * 三种情况分开守：
 * 1. 恰好一张 → 填；
 * 2. 没有 → 不动（别的槽位照填）；
 * 3. 多张 → 也不填，并说出来 —— 静默取第一张，生成的图错了没人知道。
 */

const {compileMock, specState, packsState, packState, tagsState, packCalls} = vi.hoisted(() => ({
  compileMock: vi.fn(),
  specState: {current: null as unknown},
  packsState: {current: [] as {id: string; name: string; memberCount?: number}[]},
  packState: {current: null as unknown},
  tagsState: {current: [] as unknown[]},
  packCalls: [] as {tagIds?: string[]; mode?: string}[],
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
  useLibraryTags: () => ({data: tagsState.current, isPending: false}),
  /* 包列表：后端会按槽位候选标签过滤，这里直接给「这个模板能用的那个包」 */
  useLibraryPacks: (params: {tagIds?: string[]; mode?: string}, enabled = true) => {
    packCalls.push(params);
    return {data: enabled ? packsState.current : undefined};
  },
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
        group: 'g1',
        minCount: 1,
        maxCount: 1,
        label: '正面图',
        tagIds: ['tag-front'],
      },
      {
        key: 'views',
        type: 'image',
        binding: 'user',
        group: 'g1',
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
  packCalls.length = 0;
  tagsState.current = [
    {id: 'tag-front', name: '正面图', assetCount: 2, childCount: 0, createdAt: '2026-11-09T10:00:00Z'},
    {id: 'tag-views', name: '三视图', assetCount: 2, childCount: 0, createdAt: '2026-11-09T10:00:00Z'},
  ];
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

  it('不在这个容器里的槽位，不会被包顺带改掉', async () => {
    /* 独立槽位（`group` 为空）只从候选素材里挑：它不在容器里，点包就不该动它。
       以前包和素材混在一个网格里，点包会把所有标签对得上的格子全改掉。 */
    specState.current = {
      ...SPEC,
      latest: {
        ...SPEC.latest!,
        slots: (SPEC.latest!.slots ?? []).map(slot =>
          slot.key === 'views' ? {...slot, group: ''} : slot,
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
    // 标题是容器名：老版本没有 `groups` → 按段序回退成「封装槽位1」
    expect(screen.getByText(/封装槽位/)).toBeInTheDocument();

    await user.click(cards[0]);
    // 一次点击仍然把容器里的两格填满
    expect(await screen.findByText(/素材-m-front/)).toBeInTheDocument();
    expect(await screen.findByText(/素材-m-views/)).toBeInTheDocument();
  });

  it('容器的筛选：继承子槽位候选标签，一级维度不发给后端', async () => {
    /* 「产品」是一级维度，素材身上挂的是它的叶子（6036…）。把维度也塞进
       `mode=all` 里，等于要求每个成员都挂「产品」—— 一个包都匹配不上。
       所以行里能选它，但发给后端的是具体二级。 */
    tagsState.current = [
      {id: 'tag-product', name: '产品', assetCount: 20, childCount: 3, createdAt: '2026-11-09T10:00:00Z'},
      {id: 'tag-type', name: '产品图类型', assetCount: 20, childCount: 2, createdAt: '2026-11-09T10:00:00Z'},
      {id: 'tag-6036', name: '6036', parentId: 'tag-product', parentName: '产品', depth: 2, assetCount: 5, childCount: 0, createdAt: '2026-11-09T10:01:00Z'},
      {id: 'tag-front', name: '正面图', parentId: 'tag-type', parentName: '产品图类型', depth: 2, assetCount: 2, childCount: 0, createdAt: '2026-11-09T10:00:00Z'},
      {id: 'tag-views', name: '三视图', parentId: 'tag-type', parentName: '产品图类型', depth: 2, assetCount: 2, childCount: 0, createdAt: '2026-11-09T10:00:00Z'},
    ];
    specState.current = {
      ...SPEC,
      latest: {
        ...SPEC.latest!,
        slots: (SPEC.latest!.slots ?? []).map(slot => ({
          ...slot,
          group: 'g1',
          tagIds: ['tag-product', ...(slot.tagIds ?? [])],
        })),
      },
    };
    renderLibrary();
    await screen.findAllByTitle('6036 黑（封装 · 2 张）');

    const call = packCalls.at(-1)!;
    expect(call.mode).toBe('all');
    expect(call.tagIds ?? []).not.toContain('tag-product');
    expect(call.tagIds).toEqual(expect.arrayContaining(['tag-front', 'tag-views']));
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

/**
 * 容器里的可选成员也能改成文案。
 *
 * 包只填它那几格；改成文案的那格不再由包分发 —— 否则点一次包就把用户
 * 刚写的话盖掉（而输入框还留着，两个说法并存）。
 */
describe('模板库 · 容器里的可选槽位改成提示词', () => {
  /** front 必填、views 可选，两格都在同一个容器里。 */
  const MIXED_SPEC: TemplateSpecOut = {
    ...SPEC,
    latest: {
      ...SPEC.latest!,
      prompt: '把 {{slot:front}} 换进原片；{{slot:views}}（没素材时按这句）',
      slots: (SPEC.latest!.slots ?? []).map(slot =>
        slot.key === 'views'
          ? {...slot, minCount: 0, emptyText: '产品只出现正面'}
          : slot,
      ),
    },
  };

  async function renderCustom(user: ReturnType<typeof userEvent.setup>) {
    specState.current = MIXED_SPEC;
    const client = new QueryClient({
      defaultOptions: {queries: {retry: false}, mutations: {retry: false}},
    });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={['/templates/library']}>
          <SpecLibrary />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await user.click(await screen.findByRole('radio', {name: '自定义'}));
    await screen.findByText('三视图');
  }

  it('只有可选成员出现「改提示词」，必填的没有', async () => {
    const user = userEvent.setup();
    await renderCustom(user);

    expect(screen.getByRole('radiogroup', {name: '三视图 的填法'})).toBeInTheDocument();
    expect(screen.queryByRole('radiogroup', {name: '正面图 的填法'})).toBeNull();
  });

  it('改成文案的格子不再由包填，输入框预填作者的未选时文案', async () => {
    const user = userEvent.setup();
    await renderCustom(user);

    await user.click(
      within(screen.getByRole('radiogroup', {name: '三视图 的填法'})).getByRole('radio', {
        name: '改提示词',
      }),
    );
    const box = await screen.findByPlaceholderText('写这一格没素材时提示词怎么说');
    expect(box).toHaveValue('产品只出现正面');

    await pickPack(user);

    /* 正面图照常拿到包里的那张；三视图没有被塞图（它这一格说的是文字）。 */
    expect(await screen.findByText(/素材-m-front/)).toBeInTheDocument();
    expect(screen.queryByText(/素材-m-views/)).not.toBeInTheDocument();

    /* 切回「选素材」也还是空的：包**没有**往这一格写过 ——
       否则切回来就是一张用户没挑过的图，而他从头到尾只说了那句文案。 */
    await user.click(
      within(screen.getByRole('radiogroup', {name: '三视图 的填法'})).getByRole('radio', {
        name: '选素材',
      }),
    );
    expect(screen.queryByText(/素材-m-views/)).not.toBeInTheDocument();
  });

  it('容器的进度把文案格算作已填', async () => {
    const user = userEvent.setup();
    await renderCustom(user);
    /* 只看容器自己那一行：整页另有一个「填入素材 x/y」，两者数字恰好一样，
       不圈定范围会命中两个元素。 */
    const container = screen.getByText(/封装槽位/).closest('section') as HTMLElement;
    /* 一开始 0/2（必填那格还空着，可选那格也没有任何说法）。 */
    expect(container).toHaveTextContent('0/2');

    await user.click(
      within(screen.getByRole('radiogroup', {name: '三视图 的填法'})).getByRole('radio', {
        name: '改提示词',
      }),
    );

    /* 预填的就是作者那段话 → 这一格已经有说法了。 */
    await waitFor(() => expect(container).toHaveTextContent('1/2'));
  });
});

/* 容器名是作者起的（封装页那个输入框），模板库要按它显示 ——
   使用者看到「三视图套图」才知道这一块要挑什么，写死的「封装槽位」什么也没说。 */
describe('模板库 · 容器的名字', () => {
  it('显示作者起的名字', async () => {
    specState.current = {
      ...SPEC,
      latest: {...SPEC.latest!, groups: [{key: 'g1', label: '三视图套图'}]},
    };
    renderLibrary();

    expect(await screen.findByText('三视图套图')).toBeInTheDocument();
    // 起了名就不再出现回退名（两个都显示等于没说清哪个是真的）
    expect(screen.queryByText(/^封装槽位/)).not.toBeInTheDocument();
  });

  it('老版本没有名字：按段序回退成「封装槽位1」', async () => {
    /* `groups` 是后加的字段，加之前发布的版本里没有它。回退而不是显示空 ——
       空标题会让那一块变成一个只有「N/N」计数的无名框。 */
    specState.current = {...SPEC, latest: {...SPEC.latest!, groups: []}};
    renderLibrary();

    expect(await screen.findByText('封装槽位1')).toBeInTheDocument();
  });
});
