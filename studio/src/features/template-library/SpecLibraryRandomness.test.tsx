import {QueryClient, QueryClientProvider} from '@tanstack/react-query';
import {render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {MemoryRouter} from 'react-router-dom';
import {beforeEach, describe, expect, it, vi} from 'vitest';
import type {TemplateSpecOut} from '@/shared/api/schema';
import {SpecLibrary} from './SpecLibrary';

const {compileMock, createTaskMock, generateImageMock, modelsMock, specState} =
  vi.hoisted(() => ({
    compileMock: vi.fn(),
    createTaskMock: vi.fn(),
    generateImageMock: vi.fn(),
    modelsMock: vi.fn(),
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

vi.mock('@/entities/model/api/model.queries', () => ({
  useModels: modelsMock,
}));

vi.mock('@/entities/library/api/library.api', () => ({
  libraryApi: {},
}));

vi.mock('@/entities/library/api/library.queries', () => ({
  useLibraryDetail: (id: string) => ({
    data: {
      name: id === 'r1' ? '参考图一.png' : id === 'r2' ? '参考图二.png'
        : id === 'a1' ? '商品图.png' : id === 'a2' ? '人物图.png' : '素材.png',
    },
  }),
  /* 候选集标签要在词表里，否则 scopeFilterIds 会把 tag-copy 过滤掉，
     两个槽位的网格就分不出彼此了。 */
  useLibraryTags: () => ({
    data: [{
      id: 'tag-copy', name: '参考', assetCount: 2, childCount: 0,
      createdAt: '2026-11-09T10:00:00Z',
    }],
    isPending: false,
  }),
  /* hero 槽无标签 → 商品图/人物图；copy 槽有 tag-copy → 参考图一/二。
     两个槽位候选不同，按标题找素材才不会命中多个。 */
  /* 槽位网格里还会列封装包；这些用例不关心，给空列表 */
  /* 包预览浮层用的详情查询（悬停才 enabled，这里不关心内容） */
  useLibraryPack: () => ({data: undefined, isError: false, error: null}),
  useLibraryPacks: () => ({data: []}),
  useLibrarySelection: (params: {tagIds?: string[]}) => ({
    data: {
      items: params.tagIds?.includes('tag-copy')
        ? [
            {id: 'r1', name: '参考图一.png', kind: 'image'},
            {id: 'r2', name: '参考图二.png', kind: 'image'},
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

/** 带「必填 + 可选」的模板；作者另写了一段随机档提示词。 */
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
    randomPrompt: '夜景版：把原片里的商品换成 {{Mixed 1}}，霓虹灯打亮',
    slots: [
      {
        key: 'hero',
        type: 'image',
        binding: 'user',
        minCount: 1,
        maxCount: 1,
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
        emptyText: '详细的场景描述',
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

async function switchTo(user: ReturnType<typeof userEvent.setup>, mode: string) {
  await user.click(await screen.findByRole('radio', {name: mode}));
}

/** 提交一次（过确认框），返回 compile 收到的入参。 */
async function submitPayload(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', {name: /生成视频/}));
  const dialog = await screen.findByRole('dialog');
  await user.click(within(dialog).getByRole('button', {name: '生成视频'}));
  await waitFor(() => expect(compileMock).toHaveBeenCalled());
  await waitFor(() => expect(dialog).not.toBeInTheDocument());
  return compileMock.mock.calls.at(-1)?.[0]?.payload;
}

describe('模板库 · 随机性三档', () => {
  it('三档就在界面上，名字是「随机 / 均衡 / 自定义」', async () => {
    renderLibrary();
    await waitFor(() => expect(screen.getByText('商品图')).toBeInTheDocument());
    const group = screen.getByRole('radiogroup', {name: '随机性'});
    expect(within(group).getByRole('radio', {name: '随机'})).toBeInTheDocument();
    expect(within(group).getByRole('radio', {name: '均衡'})).toBeInTheDocument();
    expect(within(group).getByRole('radio', {name: '自定义'})).toBeInTheDocument();
  });

  it('默认均衡：可选槽位不出现', async () => {
    renderLibrary();
    await waitFor(() => expect(screen.getByText('商品图')).toBeInTheDocument());
    expect(screen.queryByText('参考图')).not.toBeInTheDocument();
  });

  it('随机档：可选槽位同样不出现（不传素材，整段用随机档提示词）', async () => {
    const user = userEvent.setup();
    renderLibrary();
    await waitFor(() => expect(screen.getByText('商品图')).toBeInTheDocument());
    await switchTo(user, '随机');
    expect(screen.queryByText('参考图')).not.toBeInTheDocument();
  });

  it('自定义档：可选槽位出现，由使用者挑', async () => {
    const user = userEvent.setup();
    renderLibrary();
    await waitFor(() => expect(screen.getByText('商品图')).toBeInTheDocument());
    await switchTo(user, '自定义');
    expect(screen.getByText('参考图')).toBeInTheDocument();
  });

  it('随机档提交：不传可选槽位素材，但要后端换成随机档提示词', async () => {
    const user = userEvent.setup();
    renderLibrary();
    await waitFor(() => expect(screen.getByText('商品图')).toBeInTheDocument());
    /* 先在自定义档挑了 copy 素材，再切到随机档 —— 这样"随机档不传 copy"
       才是真判别：若抑制失效，之前挑的那张会被带进提交。 */
    await switchTo(user, '自定义');
    await user.click(await screen.findByTitle('参考图一.png'));
    await switchTo(user, '随机');
    await user.click(await screen.findByTitle('商品图.png')); // 填必填槽位
    const payload = await submitPayload(user);
    expect(payload.slotValues.hero).toContain('a1');
    expect(payload.slotValues.copy).toBeUndefined();
    expect(payload.useRandomPrompt).toBe(true);
  });

  it('均衡档提交：不换提示词，走主提示词', async () => {
    const user = userEvent.setup();
    renderLibrary();
    await waitFor(() => expect(screen.getByText('商品图')).toBeInTheDocument());
    await user.click(await screen.findByTitle('商品图.png'));
    const payload = await submitPayload(user);
    expect(payload.useRandomPrompt).toBe(false);
  });

  it('自定义档提交：传使用者挑的可选素材，不换提示词', async () => {
    const user = userEvent.setup();
    renderLibrary();
    await waitFor(() => expect(screen.getByText('商品图')).toBeInTheDocument());
    await switchTo(user, '自定义');
    await user.click(await screen.findByTitle('商品图.png'));
    // 可选槽位也挑一张（参考图一，与 hero 的候选分开）
    await user.click(await screen.findByTitle('参考图一.png'));
    const payload = await submitPayload(user);
    expect(payload.useRandomPrompt).toBe(false);
    expect(payload.slotValues.copy).toContain('r1');
  });

  it('三档随便切，必填槽位一直在', async () => {
    const user = userEvent.setup();
    renderLibrary();
    await waitFor(() => expect(screen.getByText('商品图')).toBeInTheDocument());
    for (const mode of ['随机', '自定义', '均衡', '随机']) {
      await switchTo(user, mode);
      expect(screen.getByText('商品图')).toBeInTheDocument();
    }
  });

  it('切档不弄丢自定义档挑的素材（随机档只是不提交它）', async () => {
    const user = userEvent.setup();
    renderLibrary();
    await waitFor(() => expect(screen.getByText('商品图')).toBeInTheDocument());
    await switchTo(user, '自定义');
    await user.click(await screen.findByTitle('参考图一.png'));
    await switchTo(user, '随机');
    await switchTo(user, '自定义');
    expect(screen.getByTitle('参考图一.png').querySelector('.lucide-check')).not.toBeNull();
  });

  /**
   * 这三句话是**用户给的原文**（2026-11-22），所以逐字钉住：它们讲的是
   * 「生成出来长什么样」，跟界面能不能点、哪几格能填是两回事，改一个字都
   * 可能让「随机会不会换台词」这件事被说反。
   */
  it('控件下面跟着档位换一句话，说明这一档会生成什么', async () => {
    const user = userEvent.setup();
    renderLibrary();
    await waitFor(() => expect(screen.getByText('商品图')).toBeInTheDocument());

    // 默认均衡
    expect(screen.getByText('每次生成和演示案例相似的内容')).toBeInTheDocument();

    await switchTo(user, '自定义');
    expect(
      screen.getByText('通过参考图和提示词精准控制人物、服装、场景等元素'),
    ).toBeInTheDocument();
    expect(screen.queryByText('每次生成和演示案例相似的内容')).not.toBeInTheDocument();

    await switchTo(user, '随机');
    expect(
      screen.getByText('随机的人物、服装、场景等，脚本核心人物动作、台词固定不变'),
    ).toBeInTheDocument();
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
    expect(screen.getByRole('radiogroup', {name: '随机性'})).toBeInTheDocument();
  });

  it('模板没配随机档提示词时，随机档置灰并写出原因', async () => {
    specState.current = {
      ...SPEC,
      latest: {...SPEC.latest!, randomPrompt: ''},
    };
    renderLibrary();
    await waitFor(() => expect(screen.getByText('商品图')).toBeInTheDocument());

    const random = within(screen.getByRole('radiogroup', {name: '随机性'})).getByRole(
      'radio',
      {name: '随机'},
    );
    expect(random).toBeDisabled();
    // 死按钮必须给原因：只说"不可用"等于让用户去猜是不是坏了
    expect(screen.getByText(/未配置随机档提示词/)).toBeInTheDocument();
  });
});

/**
 * 可选槽位「让给文案」有个例外：提示词里按「第 N 张」引用了它。
 * 那种槽位不能不传素材（传了编译期必然 400），与档位无关。
 */
describe('模板库 · 被索引引用的例外槽位', () => {
  it('被「{{slot:copy.1}}」引用：随机档下仍然显示，且算进缺料', async () => {
    const user = userEvent.setup();
    specState.current = {
      ...SPEC,
      latest: {
        ...SPEC.latest!,
        prompt: '把 {{slot:copy.1}} 里的东西换成 {{Mixed 1}}',
        /* 随机档用的是**另一段**提示词，所以「钉住」要在那一段里也有 ——
           只看主提示词会误以为随机档可以省掉它。 */
        randomPrompt: '夜景：把 {{slot:copy.1}} 里的东西换成 {{Mixed 1}}',
        slots: SPEC.latest!.slots,
      },
    };
    renderLibrary();
    expect(await screen.findByText('参考图')).toBeInTheDocument();
    await switchTo(user, '随机');
    expect(screen.getByText('参考图')).toBeInTheDocument();
    expect(screen.getByRole('button', {name: /还缺 2 项素材/})).toBeDisabled();
    await user.click(await screen.findByTitle('商品图.png'));
    expect(screen.getByRole('button', {name: /还缺 1 项素材/})).toBeDisabled();
  });

  it('被单张引用且写了「未选时文案」：可以省（随机档下不显示）', async () => {
    specState.current = {
      ...SPEC,
      latest: {
        ...SPEC.latest!,
        prompt: '把 {{slot:copy}} 里的东西换成 {{Mixed 1}}',
        slots: SPEC.latest!.slots,
      },
    };
    renderLibrary();
    await waitFor(() => expect(screen.getByText('商品图')).toBeInTheDocument());
    await switchTo(userEvent.setup(), '随机');
    expect(screen.queryByText('参考图')).not.toBeInTheDocument();
  });

  /* 判别点：两段提示词的「钉住」是**各算各的**。主提示词没引用、随机档那段
     引用了 → 随机档下这张必须显示（不显示就会在编译期 400）。
     拿主提示词统一判断的写法，会在这里把槽位误藏。 */
  it('只有随机档那段钉住时，切到随机档要显示该槽位', async () => {
    const user = userEvent.setup();
    specState.current = {
      ...SPEC,
      latest: {
        ...SPEC.latest!,
        prompt: '把原片里的商品换成 {{Mixed 1}}',
        randomPrompt: '夜景：把 {{slot:copy.1}} 里的东西换掉',
        slots: SPEC.latest!.slots,
      },
    };
    renderLibrary();
    await waitFor(() => expect(screen.getByText('商品图')).toBeInTheDocument());
    expect(screen.queryByText('参考图')).not.toBeInTheDocument();

    await switchTo(user, '随机');
    expect(screen.getByText('参考图')).toBeInTheDocument();
  });
});
