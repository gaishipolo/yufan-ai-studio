import {QueryClient, QueryClientProvider} from '@tanstack/react-query';
import {render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {MemoryRouter} from 'react-router-dom';
import {beforeEach, describe, expect, it, vi} from 'vitest';
import type {TemplateSpecOut} from '@/shared/api/schema';
import {SpecLibrary} from './SpecLibrary';

const {compileMock, createTaskMock, generateImageMock, modelsMock} = vi.hoisted(() => ({
  compileMock: vi.fn(),
  createTaskMock: vi.fn(),
  generateImageMock: vi.fn(),
  modelsMock: vi.fn(),
}));

vi.mock('@/entities/template-spec/api/spec.queries', () => ({
  usePublishedSpecs: () => ({data: {items: [SPEC], total: 1}, isLoading: false}),
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

/* 素材网格：给一张图，好让「填满必填槽位」这一步有东西可点。 */
vi.mock('@/entities/library/api/library.queries', () => ({
  useLibraryDetail: () => ({data: {name: '商品图.png'}}),
  useLibraryTags: () => ({data: [], isPending: false}),
  useLibrarySelection: () => ({
    data: {items: [{id: 'a1', name: '商品图.png', kind: 'image'}], total: 1},
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
