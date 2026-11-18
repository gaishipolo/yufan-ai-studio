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

  function renderOptionalLibrary() {
    specState.current = OPTIONAL_SPEC;
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
});
