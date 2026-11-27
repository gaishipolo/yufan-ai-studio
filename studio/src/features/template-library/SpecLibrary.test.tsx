import {QueryClient, QueryClientProvider} from '@tanstack/react-query';
import {render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {MemoryRouter} from 'react-router-dom';
import {beforeEach, describe, expect, it, vi} from 'vitest';
import type {TagOut, TemplateSpecOut} from '@/shared/api/schema';
import {SpecLibrary} from './SpecLibrary';

const {compileMock, createTaskMock, generateImageMock, modelsMock, specState, publishedState, publishedTagCalls, tagState} = vi.hoisted(() => ({
  compileMock: vi.fn(),
  createTaskMock: vi.fn(),
  generateImageMock: vi.fn(),
  modelsMock: vi.fn(),
  /* 当前发布的规格。用例可以换掉它来测另一种槽位配置。 */
  specState: {current: null as unknown},
  publishedState: {
    isError: false,
    error: null as unknown,
    refetch: vi.fn(),
    emptyWhenFiltered: false,
  },
  /* 筛选条选中后传给 usePublishedSpecs 的 tagIds —— 守卫「真的发后端筛了」。 */
  publishedTagCalls: [] as string[][],
  tagState: {current: [] as TagOut[]},
}));

vi.mock('@/entities/template-spec/api/spec.queries', () => ({
  usePublishedSpecs: (tagIds: string[] = []) => {
    publishedTagCalls.push(tagIds);
    return {
      data: publishedState.isError
        ? undefined
        : {
            items:
              publishedState.emptyWhenFiltered && tagIds.length
                ? []
                : [specState.current],
            total: publishedState.emptyWhenFiltered && tagIds.length ? 0 : 1,
          },
      isLoading: false,
      isError: publishedState.isError,
      error: publishedState.error,
      isFetching: false,
      refetch: publishedState.refetch,
    };
  },
  useTemplateTags: () => ({data: tagState.current, isPending: false}),
  useSpecCompile: () => ({mutateAsync: compileMock, isPending: false}),
}));

vi.mock('@/entities/task/api/task.queries', () => ({
  useCreateTask: () => ({mutateAsync: createTaskMock, isPending: false}),
}));

vi.mock('@/entities/image/api/image.api', () => ({
  useGenerateImage: () => ({mutateAsync: generateImageMock, isPending: false}),
}));

/* 模型目录用真形状（LibTV：声明了计费模式，且它是 userSelectable）。 */
vi.mock('@/entities/model/api/model.queries', () => ({
  useModels: modelsMock,
}));

/* 素材网格：给一张图，好让「填满必填槽位」这一步有东西可点。
   **按候选集（tagIds）分开返回**：可选槽位圈了 `tag-person`，
   它的候选网格与必填槽位的不一样 —— 否则按名字找素材会命中多个元素。 */
vi.mock('@/entities/library/api/library.queries', () => ({
  libraryKeys: {
    all: ['library'],
    selections: () => ['library', 'selection'],
    selection: (params: object) => ['library', 'selection', params],
  },
  useLibraryDetail: (id: string) => ({
    data: {name: id === 'p1' ? '人物图.png' : '商品图.png'},
  }),
  useLibraryTags: () => ({
    data: [
      {
        id: 'tag-person',
        name: '人物',
        assetCount: 1,
        childCount: 0,
        createdAt: '2026-11-09T10:00:00Z',
      },
    ],
    isPending: false,
  }),
  /* 槽位网格里还会列封装包；这些用例不关心，给空列表 */
  /* 包预览浮层用的详情查询（悬停才 enabled，这里不关心内容） */
  useLibraryPack: () => ({data: undefined, isError: false, error: null}),
  useLibraryPacks: () => ({data: []}),
  useLibrarySelection: (params: {tagIds?: string[]}) => ({
    data: {
      items: params.tagIds?.includes('tag-person')
        ? [{id: 'p1', name: '人物图.png', kind: 'image'}]
        : [{id: 'a1', name: '商品图.png', kind: 'image'}],
      total: 1,
    },
    isLoading: false,
    isSuccess: true,
  }),
  useLibrarySelectionInfinite: () => ({data: {pages: []}, hasNextPage: false}),
  useLibraryLargePreview: () => ({url: undefined}),
}));

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
        key: 'product',
        type: 'image',
        binding: 'user',
        minCount: 1,
        maxCount: 3,
        label: '商品图',
        tagIds: [],
      },
      /* 作者固定好的原片：它也会被编译进提示词、一起发出去，
         所以确认框里的素材数要算它 —— 只算使用者填的那几个会少报。 */
      {
        key: 'source',
        type: 'video',
        binding: 'fixed',
        minCount: 1,
        maxCount: 1,
        label: '原片',
        assetId: 'a9',
        tagIds: [],
      },
    ],
  },
};

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
    aspectRatio: {type: 'enum', options: ['9:16', '16:9', '1:1'], default: '9:16'},
    billingMode: {
      type: 'enum',
      options: ['free', 'credits'],
      default: 'free',
      label: '计费模式',
      userSelectable: true,
      description: '免费队列 / 积分加速',
    },
  },
};

/** 模板独立的两级标签词表（与素材库词表无关）。 */
const TEMPLATE_TAGS: TagOut[] = [
  {id: 'tt-product', name: '产品', depth: 1, specCount: 2},
  {id: 'tt-style', name: '画面风格', depth: 1, specCount: 1},
  {
    id: 'tt-6036',
    name: '6036',
    parentId: 'tt-product',
    parentName: '产品',
    depth: 2,
    specCount: 1,
  },
  {
    id: 'tt-real',
    name: '写实',
    parentId: 'tt-style',
    parentName: '画面风格',
    depth: 2,
    specCount: 1,
  },
];

beforeEach(() => {
  specState.current = SPEC;
  tagState.current = TEMPLATE_TAGS;
  publishedState.emptyWhenFiltered = false;
  publishedTagCalls.length = 0;
  modelsMock.mockImplementation((capability: string) =>
    capability === 'video' ? [LIBTV] : [],
  );
  compileMock.mockResolvedValue({
    version: 3,
    model: 'libtv-wan3-replacement',
    prompt: '把原片里的商品换成 {{Mixed 1}}',
    params: {durationSec: 5, aspectRatio: '9:16'},
    referenceLibraryIds: [],
    textLibraryIds: [],
  });
  createTaskMock.mockResolvedValue({id: 't1', title: '商品替换'});
  generateImageMock.mockResolvedValue({id: 'i1'});
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

/** 填满必填槽位：点素材网格里的那张图。 */
async function fillRequiredSlot(user: ReturnType<typeof userEvent.setup>) {
  const item = await screen.findByTitle('商品图.png');
  await user.click(item);
}

describe('模板库：生成前的二次确认', () => {
  it('点「生成视频」先弹确认框，此时还没提交任何东西', async () => {
    const user = userEvent.setup();
    renderLibrary();
    await fillRequiredSlot(user);

    await user.click(screen.getByRole('button', {name: /生成视频/}));

    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('确认生成视频？')).toBeInTheDocument();
    expect(compileMock).not.toHaveBeenCalled();
    expect(createTaskMock).not.toHaveBeenCalled();
  });

  it('确认框里摊开这次会提交什么：模板、模型、素材数', async () => {
    const user = userEvent.setup();
    renderLibrary();
    await fillRequiredSlot(user);

    await user.click(screen.getByRole('button', {name: /生成视频/}));

    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('商品替换 v3');
    expect(dialog).toHaveTextContent('LibTV · 720P 视频替换');
    /* 2 项 = 使用者填的 1 张商品图 + 作者固定好的 1 段原片。
       只数使用者填的那些会写成「1 项」——那与真正发出去的东西对不上。 */
    expect(dialog).toHaveTextContent('2 项');
  });

  it('取消确认框不会提交', async () => {
    const user = userEvent.setup();
    renderLibrary();
    await fillRequiredSlot(user);

    await user.click(screen.getByRole('button', {name: /生成视频/}));
    await user.click(await screen.findByRole('button', {name: '取消'}));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(compileMock).not.toHaveBeenCalled();
    expect(createTaskMock).not.toHaveBeenCalled();
  });

  it('确认后才真的提交，并关掉确认框', async () => {
    const user = userEvent.setup();
    renderLibrary();
    await fillRequiredSlot(user);

    await user.click(screen.getByRole('button', {name: /生成视频/}));
    const dialog = await screen.findByRole('dialog');
    /* 确认框里的按钮与页面上的「生成视频」同名，按作用域取框里那个。 */
    await user.click(within(dialog).getByRole('button', {name: '生成视频'}));

    await waitFor(() => expect(compileMock).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(createTaskMock).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('免费队列不显示「消耗积分」的提醒', async () => {
    const user = userEvent.setup();
    renderLibrary();
    await fillRequiredSlot(user);

    await user.click(screen.getByRole('button', {name: /生成视频/}));

    const dialog = await screen.findByRole('dialog');
    expect(dialog).not.toHaveTextContent('消耗账号积分');
  });

  it('选了积分加速就提醒会消耗积分', async () => {
    const user = userEvent.setup();
    renderLibrary();
    await fillRequiredSlot(user);

    await user.selectOptions(await screen.findByLabelText('计费模式'), 'credits');

    await user.click(screen.getByRole('button', {name: /生成视频/}));

    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('会消耗账号积分');
  });

  it('素材还缺时按钮就是灰的，点不出确认框', async () => {
    const user = userEvent.setup();
    renderLibrary();

    const button = await screen.findByRole('button', {name: /还缺 1 项素材/});
    expect(button).toBeDisabled();
    await user.click(button);

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

/**
 * 可选槽位（`minCount === 0`）：不填也能生成，提示词里用「未选时文案」兜底。
 *
 * 这一整条链路上有三处必须一致：按钮不再显示「还缺」、
 * 确认框里摆出兜底文案、提交时这段话真的进了提示词。
 *
 * **默认是「均衡」档**：可选槽位整个不出现（也不传素材）。
 * 兜底文案那条链路要在**「自定义」档**下才看得见 ——
 * 均衡档下那个槽位压根不在这一页上。
 */
describe('模板库：可选槽位不填也能生成', () => {
  const OPTIONAL_SPEC: TemplateSpecOut = {
    ...SPEC,
    latest: {
      ...SPEC.latest!,
      prompt: '把原片里的人换成 {{slot:person}}',
      slots: [
        {
          key: 'person',
          type: 'image',
          binding: 'user',
          minCount: 0,
          maxCount: 1,
          label: '人物参考',
          tagIds: ['tag-person'],
          emptyText: '主角是穿红裙的年轻女性',
        },
        SPEC.latest!.slots[1],
      ],
    },
  };

  function renderOptionalLibrary(spec: TemplateSpecOut = OPTIONAL_SPEC) {
    specState.current = spec;
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

  it('不填素材也不显示「还缺」，按钮可点', async () => {
    renderOptionalLibrary();

    const button = await screen.findByRole('button', {name: /生成视频/});
    expect(button).toBeEnabled();
    expect(screen.queryByText(/还缺/)).not.toBeInTheDocument();
  });

  it('均衡档：可选槽位整个不出现', async () => {
    renderOptionalLibrary();

    await screen.findByRole('button', {name: /生成视频/});
    expect(screen.queryByText('人物参考')).not.toBeInTheDocument();
  });

  it('自定义档：确认框里摆出这条槽位的兜底文案', async () => {
    const user = userEvent.setup();
    renderOptionalLibrary();

    /* 切到自定义：可选槽位出现，且可以「不填」。 */
    await user.click(await screen.findByRole('radio', {name: '自定义'}));
    expect(await screen.findByText('人物参考')).toBeInTheDocument();

    await user.click(screen.getByRole('button', {name: /生成视频/}));

    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('人物参考（未填）');
    expect(dialog).toHaveTextContent('主角是穿红裙的年轻女性');
  });

  it('自定义档：填了素材就不再摆兜底行', async () => {
    const user = userEvent.setup();
    renderOptionalLibrary();

    await user.click(await screen.findByRole('radio', {name: '自定义'}));
    await user.click(await screen.findByTitle('人物图.png'));
    await user.click(screen.getByRole('button', {name: /生成视频/}));

    const dialog = await screen.findByRole('dialog');
    expect(dialog).not.toHaveTextContent('人物参考（未填）');
  });

  /* 槽位**没被提示词引用**时，它的「未选时文案」是死数据 ——
     后端只在单张引用时用它（`compile.py::_fallback_or_raise`）。
     摆出来会让使用者以为这段话会进提示词。 */
  it('自定义档：槽位没被引用时不摆兜底行', async () => {
    const user = userEvent.setup();
    renderOptionalLibrary({
      ...OPTIONAL_SPEC,
      latest: {
        ...OPTIONAL_SPEC.latest!,
        // 一个槽位都没引用：`{{Mixed 1}}` 现在**是**引用（第 1 个视觉槽位），
        // 拿它当「没引用」的例子会站不住。
        prompt: '把原片里的人换掉',
      },
    });

    await user.click(await screen.findByRole('radio', {name: '自定义'}));
    expect(await screen.findByText('人物参考')).toBeInTheDocument();

    await user.click(screen.getByRole('button', {name: /生成视频/}));

    const dialog = await screen.findByRole('dialog');
    expect(dialog).not.toHaveTextContent('人物参考（未填）');
    expect(dialog).not.toHaveTextContent('主角是穿红裙的年轻女性');
  });
});

/**
 * 可选槽位的第二种填法：不挑素材，直接改写提示词里那一格。
 *
 * 作者的「未选时文案」是**通用版本**，使用者往往知道得更具体（「这次不要产品，
 * 只要人物站在海边」）。改的是插入点里那一段，不是整段提示词 ——
 * 编号展开仍在后端 `compile`，前端只把这段字送上去。
 */
describe('模板库：可选槽位改成提示词', () => {
  const EDITABLE_SPEC: TemplateSpecOut = {
    ...SPEC,
    latest: {
      ...SPEC.latest!,
      prompt: '把原片里的人换成 {{slot:person}}',
      slots: [
        {
          key: 'person',
          type: 'image',
          binding: 'user',
          minCount: 0,
          maxCount: 1,
          label: '人物参考',
          tagIds: ['tag-person'],
          emptyText: '主角是穿红裙的年轻女性',
        },
        SPEC.latest!.slots[1],
      ],
    },
  };

  function renderSpec(spec: TemplateSpecOut = EDITABLE_SPEC) {
    specState.current = spec;
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

  /** 切到自定义档：可选槽位这时才出现在右栏。 */
  async function toCustom(user: ReturnType<typeof userEvent.setup>) {
    await user.click(await screen.findByRole('radio', {name: '自定义'}));
    await screen.findByText('人物参考');
  }

  it('有插入点的可选槽位才有「选素材 / 改提示词」两格', async () => {
    const user = userEvent.setup();
    renderSpec();
    await toCustom(user);

    const segment = screen.getByRole('radiogroup', {name: '人物参考 的填法'});
    expect(within(segment).getByRole('radio', {name: '选素材'})).toBeInTheDocument();
    expect(within(segment).getByRole('radio', {name: /改提示词/})).toBeInTheDocument();
  });

  it('切到「改提示词」：输入框预填作者的未选时文案，素材网格让位', async () => {
    const user = userEvent.setup();
    renderSpec();
    await toCustom(user);

    /* 切之前网格在（那张人物图能点到）。 */
    expect(await screen.findByTitle('人物图.png')).toBeInTheDocument();

    await user.click(screen.getByRole('radio', {name: /改提示词/}));

    const box = await screen.findByRole('textbox', {name: /改提示词/});
    expect(box).toHaveTextContent('主角是穿红裙的年轻女性');
    // 网格让位给输入框：这一刻没有「挑哪张」这回事
    expect(screen.queryByTitle('人物图.png')).not.toBeInTheDocument();
    // 「已选 0/1」也不再摆（那不是这一刻的计数）
    expect(screen.queryByText(/已选 0\/1/)).not.toBeInTheDocument();
  });

  it('提交时把改写的字送进 slotTextOverrides，且这一格不传素材', async () => {
    const user = userEvent.setup();
    renderSpec();
    await toCustom(user);
    /* 先挑一张，再改成文案 —— 切回去时那张要还在（素材留在 state，只是不提交）。 */
    await user.click(await screen.findByTitle('人物图.png'));
    await user.click(screen.getByRole('radio', {name: /改提示词/}));

    const box = await screen.findByRole('textbox', {name: /改提示词/});
    await user.click(box);
      await user.keyboard('{Control>}a{/Control}');
      await user.keyboard('{Backspace}');
    await user.type(box, '不要人物，只留原片');

    await user.click(screen.getByRole('button', {name: /生成视频/}));
    const dialog = await screen.findByRole('dialog');
    await user.click(within(dialog).getByRole('button', {name: '生成视频'}));

    await waitFor(() => expect(compileMock).toHaveBeenCalledTimes(1));
    const payload = compileMock.mock.calls[0][0].payload;
    expect(payload.slotTextOverrides).toEqual({person: '不要人物，只留原片'});
    // 素材一起传上去就变成「既挑图又改文案」，后端会 400
    expect(payload.slotValues).toEqual({});
  });

  it('确认框把改写的字摆成「改写的提示词」，不写成「未填」', async () => {
    const user = userEvent.setup();
    renderSpec();
    await toCustom(user);
    await user.click(screen.getByRole('radio', {name: /改提示词/}));

    const box = await screen.findByRole('textbox', {name: /改提示词/});
    await user.click(box);
      await user.keyboard('{Control>}a{/Control}');
      await user.keyboard('{Backspace}');
    await user.type(box, '不要人物，只留原片');

    await user.click(screen.getByRole('button', {name: /生成视频/}));
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('人物参考（改写的提示词）');
    expect(dialog).toHaveTextContent('不要人物，只留原片');
    /* 「（未填）」是另一种意思：那说的是「用作者的兜底文案」。
       两个说法并存会让人以为这次用的是作者那句。 */
    expect(dialog).not.toHaveTextContent('人物参考（未填）');
    expect(dialog).not.toHaveTextContent('主角是穿红裙的年轻女性');
  });

  it('文案清空就不让生成（按钮说「还缺 1 项」，不说「素材」）', async () => {
    const user = userEvent.setup();
    renderSpec();
    await toCustom(user);
    await user.click(screen.getByRole('radio', {name: /改提示词/}));

    const box = await screen.findByRole('textbox', {name: /改提示词/});
    await user.click(box);
      await user.keyboard('{Control>}a{/Control}');
      await user.keyboard('{Backspace}');

    const button = screen.getByRole('button', {name: /还缺 1 项/});
    expect(button).toBeDisabled();
    // 空的是输入框，不是素材 —— 写成「还缺 1 项素材」会让人去网格里找
    expect(button).not.toHaveTextContent('素材');
  });

  it('切回「选素材」：刚才挑的素材还在', async () => {
    const user = userEvent.setup();
    renderSpec();
    await toCustom(user);
    await user.click(await screen.findByTitle('人物图.png'));
    expect(screen.getByText(/已选 1\/1/)).toBeInTheDocument();

    await user.click(screen.getByRole('radio', {name: /改提示词/}));
    await screen.findByRole('textbox', {name: /改提示词/});
    await user.click(screen.getByRole('radio', {name: '选素材'}));

    expect(screen.getByText(/已选 1\/1/)).toBeInTheDocument();
  });

  it('提示词里没有这一格的插入点：不给这个入口', async () => {
    const user = userEvent.setup();
    renderSpec({
      ...EDITABLE_SPEC,
      latest: {
        ...EDITABLE_SPEC.latest!,
        prompt: '把原片里的人换掉',
      },
    });
    await toCustom(user);

    /* 打了字也进不了提示词（后端 `compile` 只认单张 / 整组引用），
       摆一个不生效的输入框比不给这个能力更糟。 */
    expect(screen.queryByRole('radiogroup', {name: '人物参考 的填法'})).toBeNull();
  });
});

describe('模板库：右栏', () => {
  /**
   * 2026-11-22 用户要求去掉「改模板」：这一栏只做「填」和「生成」，
   * 跳去规格页是另一件事，改模板走左侧导航。
   */
  it('头部不出现「改模板」入口', async () => {
    renderLibrary();
    await waitFor(() => expect(screen.getByText('商品图')).toBeInTheDocument());
    expect(screen.queryByRole('link', {name: '改模板'})).toBeNull();
  });

  it('列表查询失败时展示错误态和重试按钮，而不是显示「还没有已发布的模板」', async () => {
    publishedState.isError = true;
    publishedState.error = new Error('后端不可达');
    try {
      renderLibrary();
      expect(await screen.findByText('模板加载失败')).toBeInTheDocument();
      expect(screen.queryByText('还没有已发布的模板')).toBeNull();
    } finally {
      publishedState.isError = false;
      publishedState.error = null;
    }
  });

  it('存量模板使用旧别名 libtv-wan3-replacement 时仍能按 aliases 匹配到目录模型', async () => {
    modelsMock.mockReturnValue([
      {
        ...LIBTV,
        id: 'libtv-wan3.0',
        label: 'LibTV · Wan 3.0',
        aliases: ['libtv-wan3-replacement'],
      },
    ]);
    renderLibrary();
    await waitFor(() => expect(screen.getAllByText('LibTV · Wan 3.0').length).toBeGreaterThan(0));
  });
});

/**
 * 模板独立标签：卡片展示 + 顶部两行筛选。
 *
 * 三处守卫各管一段链路，缺任何一段都会红：
 * 1. 卡片下方不再有插槽状态 chip，而是词表映射出的标签胶囊；
 * 2. 筛选条真的有两行，且一级/二级只能单选切换；
 * 3. 选中项作为 `tagIds` 交给 `usePublishedSpecs`（后端筛选），不是只在本地.hide。
 */
describe('模板库：标签筛选与卡片标签', () => {
  beforeEach(() => {
    specState.current = {...SPEC, tagIds: ['tt-6036']};
  });

  it('卡片下方不再摆插槽 chip，改摆模板标签胶囊', async () => {
    renderLibrary();

    const card = await screen.findByRole('button', {name: /商品替换/});
    expect(within(card).queryByText(/待填/)).toBeNull();
    expect(within(card).queryByText('纯提示词')).toBeNull();
    expect(within(card).queryByText('素材已固定')).toBeNull();
    /* 类型徽章也随插槽 chip 一起撤掉（右栏的槽位标签不在这张卡里）。 */
    expect(within(card).queryByText('图片')).toBeNull();
    expect(within(card).queryByText('成片')).toBeNull();
    /* 版本号角标 / 填写入口仍在。 */
    expect(within(card).getByText('v3')).toBeInTheDocument();
    expect(within(card).getByText(/填写/)).toBeInTheDocument();
    expect(within(card).getByTitle('产品 / 6036')).toBeInTheDocument();
  });

  it('筛选条两行都在，行标题说明单选替换、不再写多选组合语义', async () => {
    renderLibrary();

    const dimension = await screen.findByRole('group', {name: '模板维度标签'});
    const value = screen.getByRole('group', {name: '模板取值标签'});
    expect(within(dimension).getByText('维度').title).toContain('替换');
    expect(within(value).getByText('取值').title).toContain('单选');
    expect(within(dimension).getByText('维度').title).not.toContain('或');
    expect(within(value).getByText('取值').title).not.toContain('且');
    expect(within(dimension).getByRole('button', {name: /产品/})).toBeInTheDocument();
    expect(within(value).getByRole('button', {name: /6036/})).toBeInTheDocument();
  });

  it('选中标签后把 tagIds 交给 usePublishedSpecs 发后端筛选', async () => {
    const user = userEvent.setup();
    renderLibrary();

    const dimension = await screen.findByRole('group', {name: '模板维度标签'});
    await user.click(within(dimension).getByRole('button', {name: /产品/}));

    await waitFor(() => {
      expect(publishedTagCalls[publishedTagCalls.length - 1]).toContain('tt-product');
    });
  });

  it('点二级后父维度仍选中；请求只发最具体的二级 tagId', async () => {
    const user = userEvent.setup();
    renderLibrary();

    const dimension = await screen.findByRole('group', {name: '模板维度标签'});
    const value = screen.getByRole('group', {name: '模板取值标签'});
    await user.click(within(dimension).getByRole('button', {name: /产品/}));
    await waitFor(() =>
      expect(publishedTagCalls[publishedTagCalls.length - 1]).toEqual(['tt-product']),
    );

    await user.click(within(value).getByRole('button', {name: /6036/}));
    await waitFor(() =>
      expect(publishedTagCalls[publishedTagCalls.length - 1]).toEqual(['tt-6036']),
    );
    expect(within(dimension).getByRole('button', {name: /产品/})).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(within(value).getByRole('button', {name: /6036/})).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });

  it('再点已选二级只取消二级，一级维度保留并继续收窄取值行', async () => {
    const user = userEvent.setup();
    renderLibrary();

    const dimension = await screen.findByRole('group', {name: '模板维度标签'});
    const value = screen.getByRole('group', {name: '模板取值标签'});
    await user.click(within(dimension).getByRole('button', {name: /产品/}));
    await user.click(within(value).getByRole('button', {name: /6036/}));
    await user.click(within(value).getByRole('button', {name: /6036/}));

    await waitFor(() =>
      expect(publishedTagCalls[publishedTagCalls.length - 1]).toEqual(['tt-product']),
    );
    expect(within(dimension).getByRole('button', {name: /产品/})).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    expect(within(value).queryByRole('button', {name: /写实/})).not.toBeInTheDocument();
  });

  it('筛选无结果时保留筛选条，只在列表区显示空态', async () => {
    publishedState.emptyWhenFiltered = true;
    const user = userEvent.setup();
    renderLibrary();

    const dimension = await screen.findByRole('group', {name: '模板维度标签'});
    await user.click(within(dimension).getByRole('button', {name: /产品/}));

    await waitFor(() =>
      expect(screen.getByText('没有符合筛选条件的模板')).toBeInTheDocument(),
    );
    // 页面骨架还在：两行筛选条都保留，可以直接换标签
    expect(screen.getByRole('group', {name: '模板维度标签'})).toBeInTheDocument();
    expect(screen.getByRole('group', {name: '模板取值标签'})).toBeInTheDocument();

    await user.click(screen.getAllByRole('button', {name: '清除筛选'})[0]);
    await waitFor(() =>
      expect(screen.queryByText('没有符合筛选条件的模板')).not.toBeInTheDocument(),
    );
    expect(await screen.findByRole('button', {name: /商品替换/})).toBeInTheDocument();
  });

  it('选了维度后取值行只留该维度下的取值，没选维度时列全部', async () => {
    const user = userEvent.setup();
    renderLibrary();

    const dimension = await screen.findByRole('group', {name: '模板维度标签'});
    const value = screen.getByRole('group', {name: '模板取值标签'});
    expect(within(value).getByRole('button', {name: /6036/})).toBeInTheDocument();

    await user.click(within(dimension).getByRole('button', {name: /画面风格/}));

    await waitFor(() =>
      expect(within(value).queryByRole('button', {name: /6036/})).toBeNull(),
    );
    expect(within(value).getByRole('button', {name: /写实/})).toBeInTheDocument();
  });
});
