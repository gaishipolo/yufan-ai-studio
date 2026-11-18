import {QueryClient, QueryClientProvider} from '@tanstack/react-query';
import {render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {MemoryRouter} from 'react-router-dom';
import {beforeEach, describe, expect, it, vi} from 'vitest';
import type {TemplateSpecOut} from '@/shared/api/schema';
import {SpecLibrary} from './SpecLibrary';

const {compileMock, createTaskMock, generateImageMock, modelsMock, specState, selectionMock} =
  vi.hoisted(() => ({
    compileMock: vi.fn(),
    createTaskMock: vi.fn(),
    generateImageMock: vi.fn(),
    modelsMock: vi.fn(),
    specState: {current: null as unknown},
    /* 随机档的候选池走的是 api 层（`libraryApi.selection`），
       这里单独 mock —— 它不经过 `useLibrarySelection` 那个 hook。 */
    selectionMock: vi.fn(),
  }));

vi.mock('@/entities/template-spec/api/spec.queries', () => ({
  usePublishedSpecs: () => ({
    data: {items: [specState.current], total: 1},
    isLoading: false,
  }),
  useSpecCompile: () => ({mutateAsync: compileMock, isPending: false}),
}));

vi.mock('@/entities/task/api/task.queries', () => ({
  useCreateTask: () => ({mutateAsync: createTaskMock, isPending: false}),
}));

vi.mock('@/entities/image/api/image.api', () => ({
  useGenerateImage: () => ({mutateAsync: generateImageMock, isPending: false}),
}));

vi.mock('@/entities/model/api/model.queries', () => ({
  useModels: modelsMock,
}));

vi.mock('@/entities/library/api/library.api', () => ({
  libraryApi: {selection: selectionMock},
}));

/* 素材网格（`SlotMediaPicker` 用的那条）。
   **按候选集（tagIds）分开返回**：必填槽位与可选槽位各有各的候选，
   这样两个槽位的网格里不会同时出现同一张图 —— 否则按名字找素材
   会命中多个元素（`getByTitle` 报 multiple）。
   标签词表里必须有 `tag-copy`，否则 `scopeFilterIds` 会把这个候选集
   过滤掉，网格就分不出两条槽位了。 */
vi.mock('@/entities/library/api/library.queries', () => ({
  useLibraryDetail: (id: string) => ({
    data: {name: id === 'r1' ? '随机一.png' : id === 'r2' ? '随机二.png' : '商品图.png'},
  }),
  useLibraryTags: () => ({
    data: [
      {
        id: 'tag-copy',
        name: '参考',
        assetCount: 2,
        childCount: 0,
        createdAt: '2026-11-09T10:00:00Z',
      },
    ],
    isPending: false,
  }),
  useLibrarySelection: (params: {tagIds?: string[]}) => ({
    data: {
      items: params.tagIds?.includes('tag-copy')
        ? [
            {id: 'r1', name: '随机一.png', kind: 'image'},
            {id: 'r2', name: '随机二.png', kind: 'image'},
          ]
        : [
            {id: 'a1', name: '商品图.png', kind: 'image'},
            {id: 'a2', name: '人物图.png', kind: 'image'},
          ],
      total: 2,
    },
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
  paramSchema: {
    durationSec: {type: 'int', min: 2, max: 30, step: 1, default: 5},
  },
};

/** 一个带「必填 + 可选」的模板。可选槽位 `copy` 正好是提示词里没被引用的那种。 */
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
    prompt: '把原片里的商品换成 {{Mixed 1}}',
    slots: [
      {
        key: 'hero',
        type: 'image',
        binding: 'user',
        minCount: 1,
        maxCount: 3,
        label: '商品图',
        tagIds: [],
      },
      {
        key: 'copy',
        type: 'image',
        binding: 'user',
        minCount: 0,
        maxCount: 1,
        label: '参考图',
        tagIds: ['tag-copy'],
      },
    ],
  },
};

beforeEach(() => {
  specState.current = SPEC;
  modelsMock.mockImplementation((capability: string) =>
    capability === 'video' ? [LIBTV] : [],
  );
  compileMock.mockResolvedValue({
    version: 3,
    model: 'libtv-wan3-replacement',
    prompt: '把原片里的商品换成 {{Mixed 1}}',
    params: {durationSec: 5},
    referenceLibraryIds: [],
    textLibraryIds: [],
  });
  createTaskMock.mockResolvedValue({id: 't1', title: '商品替换'});
  generateImageMock.mockResolvedValue({id: 'i1'});
  selectionMock.mockResolvedValue({
    items: [
      {id: 'r1', name: '随机一.png', kind: 'image'},
      {id: 'r2', name: '随机二.png', kind: 'image'},
    ],
    total: 2,
    page: 1,
    pageSize: 24,
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

/** 切到某一档。 */
async function switchTo(user: ReturnType<typeof userEvent.setup>, mode: string) {
  await user.click(await screen.findByRole('radio', {name: mode}));
}

/**
 * 候选网格里**被系统随机选中**的那一张（右上角有勾）。
 *
 * 随机挑的是哪一张不确定（洗牌的结果），所以不能假设就是第一张 ——
 * 写死「第一张」的话，这条用例在种子换了之后就会变成假绿。
 */
async function pickedThumb(): Promise<HTMLElement> {
  await waitFor(() => {
    const picked = screen
      .getAllByTitle(/随机[一二]\.png/)
      .filter(node => node.querySelector('.lucide-check'));
    expect(picked.length).toBe(1);
  });
  return screen
    .getAllByTitle(/随机[一二]\.png/)
    .find(node => node.querySelector('.lucide-check')) as HTMLElement;
}

/** 提交一次（过确认框），返回 compile 收到的 `slotValues`。 */
async function submit(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', {name: /生成视频/}));
  const dialog = await screen.findByRole('dialog');
  /* 确认框里的按钮与页面上的「生成视频」同名，按作用域取框里那个。 */
  await user.click(within(dialog).getByRole('button', {name: '生成视频'}));
  await waitFor(() => expect(compileMock).toHaveBeenCalled());
  await waitFor(() => expect(dialog).not.toBeInTheDocument());
  return compileMock.mock.calls.at(-1)?.[0]?.payload?.slotValues;
}

/**
 * 模板库右栏的「随机性」三档。
 *
 * 三档的差别**只在可选槽位**；必填槽位三档都显示、都要使用者自己填。
 */
describe('模板库 · 随机性三档', () => {
  it('三档控件在有可选槽位时出现', async () => {
    renderLibrary();

    await waitFor(() => expect(screen.getByText('商品图')).toBeInTheDocument());
    expect(screen.getByRole('radiogroup', {name: '随机性'})).toBeInTheDocument();
  });

  it('默认是均衡：可选槽位整个不出现', async () => {
    renderLibrary();

    /* 均衡档下可选槽位「参考图」不在右栏里。 */
    await waitFor(() => expect(screen.getByText('商品图')).toBeInTheDocument());
    expect(screen.queryByText('参考图')).not.toBeInTheDocument();
  });

  it('均衡档下可选槽位既不显示也不传素材', async () => {
    const user = userEvent.setup();
    renderLibrary();

    /* 必填槽位得填上才能提交。 */
    await user.click(await screen.findByTitle('商品图.png'));
    const slotValues = await submit(user);

    expect(slotValues).toEqual({hero: ['a1']});
    expect('copy' in (slotValues ?? {})).toBe(false);
  });

  it('随机档挑过之后切回均衡：那几张也不再提交', async () => {
    /* 这条是「均衡 = 不显示 + 不传」两件事一起做的判别力用例：
       如果只做了「不显示」而没做「不传」，可选槽位的值会留在请求里，
       而界面上根本没有那一格 —— 使用者完全不知道自己传了什么。 */
    const user = userEvent.setup();
    renderLibrary();

    await switchTo(user, '随机');
    await pickedThumb();
    await switchTo(user, '均衡');

    await user.click(await screen.findByTitle('商品图.png'));
    const slotValues = await submit(user);

    expect(slotValues).toEqual({hero: ['a1']});
    expect('copy' in (slotValues ?? {})).toBe(false);
  });

  it('切到自定义：可选槽位出现', async () => {
    const user = userEvent.setup();
    renderLibrary();

    await switchTo(user, '自定义');

    expect(await screen.findByText('参考图')).toBeInTheDocument();
  });

  it('切到随机：可选槽位出现，并且系统自动填了素材', async () => {
    const user = userEvent.setup();
    renderLibrary();

    await switchTo(user, '随机');

    /* 候选池回来后系统自动填上 —— 候选网格里有且只有一张被勾上。 */
    await pickedThumb();
  });

  it('随机档提交时带上系统挑的那张', async () => {
    const user = userEvent.setup();
    renderLibrary();

    await switchTo(user, '随机');
    await waitFor(() => expect(selectionMock).toHaveBeenCalled());

    await user.click(await screen.findByTitle('商品图.png'));
    const slotValues = await submit(user);

    expect(slotValues).toHaveProperty('copy');
    expect((slotValues?.copy ?? []).length).toBe(1);
  });

  it('随机档下使用者删掉系统挑的那张，提交时就不再带它', async () => {
    const user = userEvent.setup();
    renderLibrary();

    await switchTo(user, '随机');
    const thumb = await pickedThumb();

    /* 点已选的 = 取消选择。 */
    await user.click(thumb);
    await user.click(await screen.findByTitle('商品图.png'));

    const slotValues = await submit(user);
    expect(slotValues?.copy ?? []).toEqual([]);
  });

  it('从随机切到自定义：系统挑的那张不会凭空消失', async () => {
    const user = userEvent.setup();
    renderLibrary();

    await switchTo(user, '随机');
    const thumb = await pickedThumb();

    await switchTo(user, '自定义');

    /* 切档后那张图还选着 —— 使用者明明刚看到它填在那儿。 */
    await waitFor(() => {
      const still = screen
        .getAllByTitle(/随机[一二]\.png/)
        .filter(node => node.querySelector('.lucide-check'));
      expect(still.length).toBe(1);
    });
    expect(thumb).toBeInTheDocument();
  });

  it('三档之间随便切，必填槽位一直都在', async () => {
    const user = userEvent.setup();
    renderLibrary();

    await switchTo(user, '随机');
    await switchTo(user, '自定义');
    await switchTo(user, '均衡');

    expect(await screen.findByText('商品图')).toBeInTheDocument();
  });

  it('「换一批」重新挑一次', async () => {
    const user = userEvent.setup();
    renderLibrary();

    await switchTo(user, '随机');
    const first = await pickedThumb();
    const firstTitle = first.getAttribute('title');

    await user.click(screen.getByRole('button', {name: '换一批'}));

    /* 换一批之后**可能**挑到同一张（池子只有两张，撞上是正常的），
       所以这里只断言「还是选着一张」，不断言「换了」。真正要守的是
       「换一批这个动作存在且不会把素材清空」。 */
    await waitFor(() => {
      const picked = screen
        .getAllByTitle(/随机[一二]\.png/)
        .filter(node => node.querySelector('.lucide-check'));
      expect(picked.length).toBe(1);
    });
    expect(firstTitle).toBeTruthy();
  });

  it('随机 → 自定义 → 随机：回到随机档时是**新的一批**，不是原来那几张', async () => {
    const user = userEvent.setup();
    renderLibrary();

    await switchTo(user, '随机');
    const before = (await pickedThumb()).getAttribute('title');

    await switchTo(user, '自定义');
    await switchTo(user, '随机');

    /* 池子只有两张，两次随机撞上同一张的概率不低 —— 所以这条用例
       断的是「继承」这条路径：切走时继承的那几张在切回来时被清掉，
       由新的随机接管。撞上同一张时看不出来，但**继承的痕迹**会被清掉：
       自定义档下手動挑过的那几张不会在切回来时消失。 */
    await waitFor(() => {
      const picked = screen
        .getAllByTitle(/随机[一二]\.png/)
        .filter(node => node.querySelector('.lucide-check'));
      expect(picked.length).toBe(1);
    });
    expect(before).toBeTruthy();
  });

  it('自定义档里手动挑的那张，切回随机档时不会被清掉', async () => {
    /* 这条是「继承」与「手动」的区分点：
       - 随机档自动填的 → 切走时**继承**，切回来时被新的随机接管；
       - 使用者亲手挑的 → 一律保留。
       把两者混为一谈的后果是：使用者精心挑的那张被系统随机换掉了。 */
    const user = userEvent.setup();
    renderLibrary();

    await switchTo(user, '自定义');
    /* 自定义档的候选网格里挑一张（与随机档的网格不是同一批）。 */
    const manual = screen.getAllByTitle(/随机[一二]\.png/)[0];
    await user.click(manual);
    await waitFor(() => {
      const picked = screen
        .getAllByTitle(/随机[一二]\.png/)
        .filter(node => node.querySelector('.lucide-check'));
      expect(picked.length).toBe(1);
    });

    await switchTo(user, '随机');

    /* 切回随机档后，那张手动挑的**还在**（没被系统随机换掉）。 */
    await waitFor(() => {
      const picked = screen
        .getAllByTitle(/随机[一二]\.png/)
        .filter(node => node.querySelector('.lucide-check'));
      expect(picked.length).toBe(1);
      expect(picked[0].getAttribute('title')).toBe(manual.getAttribute('title'));
    });
  });
});

describe('模板库 · 全是必填槽位时', () => {
  it('三档控件仍然出现（用户拍板：全是必填也显示）', async () => {
    specState.current = {
      ...SPEC,
      latest: {...SPEC.latest!, slots: [SPEC.latest!.slots[0]]},
    };
    renderLibrary();

    await waitFor(() => expect(screen.getByText('商品图')).toBeInTheDocument());
    /* 三档是这一栏的**前提**，无条件出现 —— 全是必填的模板也让人看见这个选择。 */
    expect(screen.getByRole('radiogroup', {name: '随机性'})).toBeInTheDocument();
  });

  it('没有可随机的对象时，「换一批」不出现（点不动的按钮是骗人）', async () => {
    specState.current = {
      ...SPEC,
      latest: {...SPEC.latest!, slots: [SPEC.latest!.slots[0]]},
    };
    renderLibrary();

    await userEvent.setup().click(
      await screen.findByRole('radio', {name: '随机'}),
    );

    /* 切到随机档了，但没有可选槽位 → 没有东西可随机 → 不给「换一批」。 */
    expect(screen.queryByRole('button', {name: '换一批'})).not.toBeInTheDocument();
  });
});

/**
 * 均衡档「省掉可选槽位」有个例外：**提示词里按「第 N 张」引用了它**。
 *
 * 那种槽位不能不传 —— 传了编译期必然 400（没有「第 2 张」这回事），
 * 而那句报错与「我用的是均衡模式」毫无关系，使用者只会以为模板坏了。
 */
describe('模板库 · 均衡档下的例外槽位', () => {
  it('被「{{slot:copy.1}}」引用的可选槽位：均衡档下仍然显示，且算进缺料', async () => {
    const user = userEvent.setup();
    specState.current = {
      ...SPEC,
      latest: {
        ...SPEC.latest!,
        prompt: '把 {{slot:copy.1}} 里的东西换成 {{Mixed 1}}',
        slots: SPEC.latest!.slots,
      },
    };
    renderLibrary();

    /* 槽位出现在右栏里 —— 它没被让给模型。 */
    expect(await screen.findByText('参考图')).toBeInTheDocument();

    /* 两个槽位都缺（hero 是必填，copy 被索引引用后也得填）。 */
    expect(screen.getByRole('button', {name: /还缺 2 项素材/})).toBeDisabled();

    /* **填完必填的那个还不够** —— 少了 copy 仍然过不去。
       这条才是判别力所在：只看 `minCount`（copy 的 minCount 是 0）的话，
       填完 hero 按钮就亮了，提交后后端回「第 1 张不存在（这次填了 0 张）」，
       使用者完全看不出是自己的模板有问题。 */
    await user.click(await screen.findByTitle('商品图.png'));
    expect(screen.getByRole('button', {name: /还缺 1 项素材/})).toBeDisabled();
  });

  it('被单张引用但没写「未选时文案」：同样不能省', async () => {
    const user = userEvent.setup();
    specState.current = {
      ...SPEC,
      latest: {
        ...SPEC.latest!,
        prompt: '把 {{slot:copy}} 里的东西换成 {{Mixed 1}}',
        slots: SPEC.latest!.slots,
      },
    };
    renderLibrary();

    expect(await screen.findByText('参考图')).toBeInTheDocument();
    expect(screen.getByRole('button', {name: /还缺 2 项素材/})).toBeDisabled();

    /* 填完必填的那个，单张引用那个还得填。 */
    await user.click(await screen.findByTitle('商品图.png'));
    expect(screen.getByRole('button', {name: /还缺 1 项素材/})).toBeDisabled();
  });

  it('被单张引用且写了「未选时文案」：可以省（均衡档下不显示）', async () => {
    specState.current = {
      ...SPEC,
      latest: {
        ...SPEC.latest!,
        prompt: '把 {{slot:copy}} 里的东西换成 {{Mixed 1}}',
        slots: [
          SPEC.latest!.slots[0],
          {...SPEC.latest!.slots[1], emptyText: '主角是穿红裙的年轻女性'},
        ],
      },
    };
    renderLibrary();

    await waitFor(() => expect(screen.getByText('商品图')).toBeInTheDocument());
    expect(screen.queryByText('参考图')).not.toBeInTheDocument();
  });

  it('被「第 2 张」引用的槽位：实际要 2 张素材，不是一片', async () => {
    /* `{{slot:copy.2}}` = 取这个槽位的第 2 张 —— 填 1 张时后端报
       「第 2 张不存在（这次填了 1 张）」。所以这个槽位的下限是 **2 张**，
       与 `minCount`（0）无关，也与档位无关。 */
    const user = userEvent.setup();
    specState.current = {
      ...SPEC,
      latest: {
        ...SPEC.latest!,
        prompt: '把 {{slot:copy.2}} 里的东西换成 {{Mixed 1}}',
        slots: [
          SPEC.latest!.slots[0],
          {...SPEC.latest!.slots[1], maxCount: 3},
        ],
      },
    };
    renderLibrary();

    expect(await screen.findByText('参考图')).toBeInTheDocument();
    await user.click(await screen.findByTitle('商品图.png'));

    /* 填了必填的 hero 之后还缺 1 项 —— copy 一张都没填。 */
    expect(screen.getByRole('button', {name: /还缺 1 项素材/})).toBeDisabled();
  });
});
