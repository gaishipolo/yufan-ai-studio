import {QueryClient, QueryClientProvider} from '@tanstack/react-query';
import {render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {MemoryRouter} from 'react-router-dom';
import {beforeEach, describe, expect, it, vi} from 'vitest';
import type {TemplateSpecOut} from '@/shared/api/schema';
import {SpecLibrary} from './SpecLibrary';

const {compileMock, createTaskMock, generateImageMock, modelsMock, specState} = vi.hoisted(() => ({
  compileMock: vi.fn(),
  createTaskMock: vi.fn(),
  generateImageMock: vi.fn(),
  modelsMock: vi.fn(),
  /* 当前发布的规格。用例可以换掉它来测另一种槽位配置。 */
  specState: {current: null as unknown},
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

/* 模型目录用真形状（LibTV：声明了计费模式，且它是 userSelectable）。 */
vi.mock('@/entities/model/api/model.queries', () => ({
  useModels: modelsMock,
}));

/* 素材网格：给一张图，好让「填满必填槽位」这一步有东西可点。
   **按候选集（tagIds）分开返回**：可选槽位圈了 `tag-person`，
   它的候选网格与必填槽位的不一样 —— 否则按名字找素材会命中多个元素。 */
vi.mock('@/entities/library/api/library.queries', () => ({
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

beforeEach(() => {
  specState.current = SPEC;
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
    expect(within(segment).getByRole('radio', {name: '改提示词'})).toBeInTheDocument();
  });

  it('切到「改提示词」：输入框预填作者的未选时文案，素材网格让位', async () => {
    const user = userEvent.setup();
    renderSpec();
    await toCustom(user);

    /* 切之前网格在（那张人物图能点到）。 */
    expect(await screen.findByTitle('人物图.png')).toBeInTheDocument();

    await user.click(screen.getByRole('radio', {name: '改提示词'}));

    const box = await screen.findByPlaceholderText('写这一格没素材时提示词怎么说');
    expect(box).toHaveValue('主角是穿红裙的年轻女性');
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
    await user.click(screen.getByRole('radio', {name: '改提示词'}));

    const box = await screen.findByPlaceholderText('写这一格没素材时提示词怎么说');
    await user.clear(box);
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
    await user.click(screen.getByRole('radio', {name: '改提示词'}));

    const box = await screen.findByPlaceholderText('写这一格没素材时提示词怎么说');
    await user.clear(box);
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
    await user.click(screen.getByRole('radio', {name: '改提示词'}));

    const box = await screen.findByPlaceholderText('写这一格没素材时提示词怎么说');
    await user.clear(box);

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

    await user.click(screen.getByRole('radio', {name: '改提示词'}));
    await screen.findByPlaceholderText('写这一格没素材时提示词怎么说');
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
