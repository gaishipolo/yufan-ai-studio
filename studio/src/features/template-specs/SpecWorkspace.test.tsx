import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { RouterProvider, createMemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { TemplateSpecIn, TemplateSpecOut } from '@/shared/api/schema';
import { useToastStore } from '@/shared/lib/toast';
import { Toaster } from '@/shared/ui/Toaster';
import { SpecWorkspace } from './SpecWorkspace';

/**
 * 模板封装页的列表区。
 *
 * 这个页面一直**没有测试**（近千行，编辑器部分尤其重），这里只钉住列表卡片
 * 的操作集合 —— 它是「模板在哪个状态能做什么」的唯一出口。
 *
 * 起因是一个真缺陷：停用（`disabled`）的模板，卡片脚上只剩「编辑」和「删除」。
 * 按钮条件是 `status !== 'disabled'` 才画「发布」，于是下架之后想放回去
 * **没有任何入口** —— 死胡同。
 */

const { specs, trashedSpecs, listState, mutations, canState } = vi.hoisted(() => ({
  canState: {caseWrite: true},
  specs: { current: [] as TemplateSpecOut[] },
  trashedSpecs: { current: [] as TemplateSpecOut[] },
  listState: { isError: false, error: null as unknown, refetch: vi.fn() },
  mutations: {
    /* 入参就是提交体（`TemplateSpecIn`）：有用例要断言「提交上去的提示词 /
       槽位 / 容器名是什么」。只声明 `{prompt}` 的话，断言别的字段会直接类型报错，
       而补一个 `as any` 又会把拼错的字段一起放过。 */
    save: vi.fn((_payload: TemplateSpecIn) =>
      Promise.resolve({ key: 'spec-1', revision: 2 }),
    ),
    publish: vi.fn(() => Promise.resolve({ version: 3, spec: {} })),
    disable: vi.fn(() => Promise.resolve({})),
    restore: vi.fn(() => Promise.resolve({})),
    remove: vi.fn(() => Promise.resolve({})),
  },
}));

/* 权限是输入条件之一：「设置案例」只在有 `library:case:write` 时出现。 */
vi.mock('@/shared/access/use-access', () => ({
  useCan: (permission: string) =>
    permission === 'library:case:write' ? canState.caseWrite : true,
}));

vi.mock('@/entities/template-spec/api/spec.queries', () => ({
  /* 上限来自后端：两个数说的是不同的事（存草稿 / 能不能发布）。 */
  useSpecLimits: () => ({data: {promptMax: 20000, promptExecutionLimit: 7000}}),
  useSpecs: () => ({
    data: listState.isError ? undefined : { items: specs.current, total: specs.current.length, page: 1, pageSize: 100 },
    isLoading: false,
    isError: listState.isError,
    error: listState.error,
    isFetching: false,
    refetch: listState.refetch,
  }),
  useTrashedSpecs: () => ({
    data: {
      items: trashedSpecs.current,
      total: trashedSpecs.current.length,
      page: 1,
      pageSize: 100,
    },
    isLoading: false,
    isError: false,
    error: null,
  }),
  useSpecMutations: () => ({
    validate: { mutateAsync: vi.fn(), isPending: false },
    save: { mutateAsync: mutations.save, isPending: false },
    publish: { mutateAsync: mutations.publish, isPending: false },
    disable: { mutateAsync: mutations.disable, isPending: false },
    remove: { mutateAsync: mutations.remove, isPending: false },
    restore: { mutateAsync: mutations.restore, isPending: false },
  }),
}));

vi.mock('@/entities/model/api/model.queries', () => ({
  useModels: () => [],
}));

vi.mock('@/entities/library/api/library.queries', () => ({
  useLibraryTags: () => ({ data: [] }),
  useLibraryDetail: () => ({ data: null, isLoading: false }),
}));

vi.mock('@/entities/library/api/library.api', () => ({
  libraryApi: { setPurpose: vi.fn(), selection: vi.fn() },
}));

/* 案例封面会去取素材字节：列表区用不到，替成占位。 */
vi.mock('@/entities/library/ui/CaseCover', () => ({
  CaseCover: () => <div data-testid="case-cover" />,
}));

function specOf(overrides: Partial<TemplateSpecOut> = {}): TemplateSpecOut {
  return {
    id: 'spec-1',
    key: 'spec-1',
    name: '商品图模板',
    description: '',
    ownerId: 'u-1',
    status: 'draft',
    revision: 1,
    fingerprint: 'fp',
    currentVersion: 0,
    publishedAt: null,
    disabledAt: null,
    deletedAt: null,
    createdAt: '2026-11-01T00:00:00Z',
    updatedAt: '2026-11-01T00:00:00Z',
    draft: null,
    demoAssetId: null,
    latest: null,
    ...overrides,
  } as TemplateSpecOut;
}

/* toast store 是模块级单例：成功提示现在走 toast，每个用例必须从空开始 */
beforeEach(() => {
  canState.caseWrite = true;
  for (const item of useToastStore.getState().items) {
    useToastStore.getState().dismiss(item.id);
  }
});

function renderWorkspace(rows: TemplateSpecOut[]) {
  specs.current = rows;
  /* 页面里有一个裸 `useMutation`（把素材移进案例池），它要 QueryClient。 */
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  /* 「离开前问一声」用的是 `useBlocker`，它要求 **data router** 上下文
     （项目里是 `createBrowserRouter`）。`MemoryRouter` 不行，必须是
     `createMemoryRouter` + `RouterProvider`。 */
  const router = createMemoryRouter([
    { path: '/', element: <SpecWorkspace /> },
    { path: '/elsewhere', element: <p>别的页面</p> },
  ]);
  return {
    ...render(
      <QueryClientProvider client={client}>
        <RouterProvider router={router} />
        <Toaster />
      </QueryClientProvider>,
    ),
    /* 路由切换那一条要主动导航，所以把 router 交出去 */
    router,
  };
}

/** 取到某张模板的卡片（页面上还有其他 listitem 之类，按名字定位）。 */
function cardOf(name: string) {
  const heading = screen.getByRole('button', { name });
  const card = heading.closest('div.rounded-2xl') ?? heading.parentElement?.parentElement;
  if (!card) throw new Error(`找不到 ${name} 的卡片`);
  return within(card as HTMLElement);
}

describe('模板封装 · 列表卡片的操作集合', () => {
  it('草稿：有「编辑」和「发布」，没有「停用」', () => {
    renderWorkspace([specOf({ status: 'draft' })]);

    const card = cardOf('商品图模板');
    expect(card.getByRole('button', { name: '编辑' })).toBeInTheDocument();
    expect(card.getByRole('button', { name: '发布' })).toBeInTheDocument();
    expect(card.queryByRole('button', { name: '停用' })).not.toBeInTheDocument();
    expect(card.queryByRole('button', { name: '启用' })).not.toBeInTheDocument();
  });

  it('已发布：有「发布」和「停用」', () => {
    renderWorkspace([specOf({ status: 'published', currentVersion: 2 })]);

    const card = cardOf('商品图模板');
    expect(card.getByRole('button', { name: '发布' })).toBeInTheDocument();
    expect(card.getByRole('button', { name: '停用' })).toBeInTheDocument();
  });

  /* 这条是这个测试文件存在的理由：停用态**必须**给一条回到上线的路。
     以前条件是 `status !== 'disabled'` 才画「发布」，于是下架的模板
     只剩「编辑」和「删除」—— 想放回去没有任何入口。 */
  it('已停用：有「启用」，没有「发布」「停用」', () => {
    renderWorkspace([specOf({ status: 'disabled', currentVersion: 2 })]);

    const card = cardOf('商品图模板');
    expect(card.getByRole('button', { name: '启用' })).toBeInTheDocument();
    expect(card.queryByRole('button', { name: '发布' })).not.toBeInTheDocument();
    expect(card.queryByRole('button', { name: '停用' })).not.toBeInTheDocument();
  });

  it('点「启用」走的是 publish 接口（同一个动作，只是说法不同）', async () => {
    const user = userEvent.setup();
    renderWorkspace([specOf({ status: 'disabled', currentVersion: 2 })]);

    await user.click(cardOf('商品图模板').getByRole('button', { name: '启用' }));

    expect(mutations.publish).toHaveBeenCalledWith('spec-1');
  });

  /* 停用态走幂等分支，版本号不变。这时候说「已发布 v2」会让人以为又发了一版。 */
  it('启用成功：提示说「已启用」，且走浮层 toast 不占文档流', async () => {
    const user = userEvent.setup();
    renderWorkspace([specOf({ status: 'disabled', currentVersion: 2 })]);

    await user.click(cardOf('商品图模板').getByRole('button', { name: '启用' }));

    const notice = await screen.findByText('已启用 商品图模板');
    /* 内联横幅出现/消失会顶动下面的卡片，这就是页面抖动的来源。
       断言它待在 toast 的 `role="status"` 容器里 —— 改回内联 Notice 会变红。 */
    expect(notice.closest('[role="status"]')).not.toBeNull();
    expect(screen.queryByText(/已发布 商品图模板 v/)).not.toBeInTheDocument();
  });

  it('草稿发布成功仍然说「已发布 vN」（别把两种说法混起来）', async () => {
    const user = userEvent.setup();
    renderWorkspace([specOf({ status: 'draft' })]);

    await user.click(cardOf('商品图模板').getByRole('button', { name: '发布' }));

    expect(await screen.findByText('已发布 商品图模板 v3')).toBeInTheDocument();
  });

  /* 回收站里的人只该看到「恢复」——它连「编辑」都不给（`trashed` 会禁用标题按钮）。
     这条同时确认：卡片脚上的按钮集合是被 `trashed` **整块替换**的，
     不是叠加在草稿/已发布那套上面。 */
  it('回收站视图：给的是「恢复」，不给发布/停用/启用/删除', async () => {
    const user = userEvent.setup();
    trashedSpecs.current = [
      specOf({ status: 'disabled', deletedAt: '2026-11-10T00:00:00Z' }),
    ];
    renderWorkspace([]);

    await user.click(screen.getByRole('button', { name: /回收站/ }));

    const card = cardOf('商品图模板');
    expect(card.getByRole('button', { name: /恢复/ })).toBeInTheDocument();
    expect(card.queryByRole('button', { name: '发布' })).not.toBeInTheDocument();
    expect(card.queryByRole('button', { name: '启用' })).not.toBeInTheDocument();
    expect(card.queryByRole('button', { name: '停用' })).not.toBeInTheDocument();
    expect(card.queryByRole('button', { name: '删除' })).not.toBeInTheDocument();
  });
});

/**
 * 编辑器里「提示词」的形态。
 *
 * 它**不套板块外壳**：这一页真正在写的就是提示词，和下面「槽位速览」
 * 「展示案例」不是同级信息。给它一条「提示词」标题带子，只是把输入框
 * 自己已经说明的事再说一遍。
 *
 * 「+ 文本素材」也去掉了：左栏「槽位」板块的加号菜单本来就能加任意类型。
 */
describe('模板封装 · 编辑器里的提示词', () => {
  const enterEditor = async () => {
    const user = userEvent.setup();
    renderWorkspace([specOf({ status: 'draft' })]);

    await user.click(cardOf('商品图模板').getByRole('button', { name: '编辑' }));
    return user;
  };

  /* 标题加回来了（2026-11-19 用户要求），但**只是**一行字 ——
     不再套「板块外壳」那条带渐变底 + 竖条的标题带。 */
  it('有「提示词」这行标题，但不是一整个板块外壳', async () => {
    await enterEditor();

    expect(screen.getByText('提示词')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '提示词' })).not.toBeInTheDocument();
  });

  it('提示词输入框本身还在（取消的是外壳，不是框）', async () => {
    await enterEditor();

    expect(screen.getByRole('textbox', { name: '提示词' })).toBeInTheDocument();
  });

  /* 超了就是发不出去 —— 数字本身说明不了这件事，得让它变红。 */
  it('超过可发布上限时，字数计数变红', async () => {
    const user = userEvent.setup();
    renderWorkspace([
      specOf({
        status: 'draft',
        draft: {
          key: 'spec-1',
          name: '商品图模板',
          model: 'libtv-wan3-replacement',
          prompt: 'x'.repeat(7001),
          slots: [{key: 'image_1', type: 'image', position: 0, label: '主图'}],
        },
      }),
    ]);
    await user.click(cardOf('商品图模板').getByRole('button', { name: '编辑' }));

    /* 两段提示词各有一个计数器（主 / 随机档），超限的是主提示词那一个。 */
    const counters = screen.getAllByText(new RegExp(`/\\s*7000`));
    expect(counters.some(node => node.className.includes('text-danger-deep'))).toBe(true);
  });

  it('不再有「+ 文本素材」这个重复入口（左栏加号菜单已覆盖）', async () => {
    await enterEditor();

    expect(screen.queryByRole('button', { name: /文本素材/ })).not.toBeInTheDocument();
  });

  /* 字数是一门**约束**（超了发不出去），取消板块时不能连带删掉。 */
  it('字数上限显示的是**能不能发布**的那个数（7000），不是存草稿的 20000', async () => {
    await enterEditor();

    // 两段提示词（主 / 随机档）都用同一个可发布上限
    expect(screen.getAllByText(new RegExp(`/\\s*7000`)).length).toBeGreaterThan(0);
    expect(screen.queryByText(new RegExp(`/\\s*20000`))).not.toBeInTheDocument();
  });

  /* 「槽位速览」整块取消：它的信息（已固定 / 用户填写）搬到了提示词框
     下面那排 chip 上，所以这里要确认板块真的不在了。 */
  it('没有「槽位速览」板块', async () => {
    await enterEditor();

    expect(screen.queryByRole('heading', { name: '槽位速览' })).not.toBeInTheDocument();
  });

  /* 用户报的那条：粘贴一个**正确**的引用，界面说「不要手写大括号」，
     而且那段字留在框里 —— 因为它既没进 `value`、也没人收拾 DOM。 */
  it('粘贴正确的槽位引用 → 直接变胶囊，不出红字', async () => {
    const user = await enterEditor();
    const box = screen.getByRole('textbox', { name: '提示词' });

    await user.click(box);
    await user.paste('参考 {{slot:image_1}}');

    const capsule = await waitFor(() => {
      const found = box.querySelector('[data-token]');
      expect(found).not.toBeNull();
      return found as HTMLElement;
    });
    expect(capsule).toHaveTextContent('图片 1');
    expect(box.textContent).not.toContain('{{slot:');
    expect(screen.queryByText(/不要手写大括号/)).not.toBeInTheDocument();
  });

  /* 结构变了就要重编号：把第 1 个图片槽位改成文本，它退出视觉编号，
     第 2 个槽位从 `{{Mixed 2}}` 前移到 `{{Mixed 1}}` —— 不动的话，正文里
     那个引用会静默指向**另一个槽位**，作者完全看不出来。 */
  it('把第一个槽位改成文本后，后面的编号前移，且不留悬空引用', async () => {
    const user = userEvent.setup();
    renderWorkspace([
      specOf({
        status: 'draft',
        draft: {
          key: 'spec-1',
          name: '商品图模板',
          model: 'libtv-wan3-replacement',
          prompt: 'A {{Mixed 1}} B {{Mixed 2}}',
          slots: [
            {key: 'image_1', type: 'image', position: 0, label: '主图', minCount: 1, maxCount: 1},
            {key: 'image_2', type: 'image', position: 1, label: '细节', minCount: 1, maxCount: 1},
          ],
        },
      }),
    ]);
    await user.click(cardOf('商品图模板').getByRole('button', { name: '编辑' }));

    await user.click(screen.getAllByRole('button', { name: '展开更多设置' })[0]);
    await user.selectOptions(screen.getAllByRole('combobox', { name: '类型' })[0], 'text');

    mutations.save.mockClear();
    await user.click(screen.getByRole('button', { name: '保存草稿' }));

    const sent = mutations.save.mock.calls[0][0];
    // 第 1 个槽位不再是视觉槽位 → 它那处引用变成 key 形态；第 2 个前移到 1 号
    expect(sent.prompt).toBe('A {{slot:image_1}} B {{Mixed 1}}');
    // 一条悬空引用都不许有（key 都还在）
    expect(sent.prompt).not.toMatch(/\{\{slot:(?!image_1)/);
  });

  /* 反过来：粘一个**不存在**的 key，必须拒，并且说清是哪个 key。
     前面那条改动要是把校验一起放松了（「只要是花括号就放」），这条会红。 */
  it('粘贴不存在的槽位引用 → 拒掉，并指名是哪个 key', async () => {
    const user = await enterEditor();
    const box = screen.getByRole('textbox', { name: '提示词' });

    await user.click(box);
    await user.paste('{{slot:image_9}}');

    expect(await screen.findByText(/没有叫「image_9」的槽位/)).toBeInTheDocument();
    // 没写进去：框里还是空的
    expect(box.textContent).toBe('');
  });
});

/**
 * 离开编辑页时的未保存保护。
 *
 * 三个出口都要挡：站内切页面（路由）、关标签（beforeunload）、
 * 点左上角「返回模板列表」。只挡一个等于没挡 —— 用户从另一个出口走掉，
 * 改的东西一样白费。
 */
describe('模板封装 · 未保存就离开', () => {
  const enterEditor = async (rows = [specOf({ status: 'draft' })]) => {
    const user = userEvent.setup();
    renderWorkspace(rows);
    await user.click(cardOf('商品图模板').getByRole('button', { name: '编辑' }));
    return user;
  };

  it('没改过就直接走，不弹确认', async () => {
    const user = await enterEditor();

    await user.click(screen.getByRole('button', { name: '返回模板列表' }));

    expect(screen.queryByRole('dialog', { name: '还有未保存的修改' })).not.toBeInTheDocument();
    // 回到列表了
    expect(screen.getByRole('button', { name: '新建模板' })).toBeInTheDocument();
  });

  it('只改了容器名也要算「有未保存改动」', async () => {
    /* 容器名进了草稿指纹。漏进去的表现是：作者改完名字点返回，
       页面一句不问就走 —— 名字丢了，而「刚刚改过」的画面还在眼前。

       **必须打开一份已经有两个容器的草稿**：从空模板加容器的话，加槽位
       本身就把指纹改掉了，改不改名都会 dirty，这条用例谁来写都是绿的。 */
    const user = await enterEditor([
      specOf({
        status: 'draft',
        draft: {
          key: 'spec-1',
          name: '商品图模板',
          model: 'libtv-wan3-replacement',
          prompt: '{{Mixed 1}} 与 {{Mixed 2}}',
          slots: [
            {key: 'image_1', type: 'image', group: 'g1', position: 0, label: '正面图'},
            {key: 'image_2', type: 'image', group: 'g1', position: 1, label: '三视图'},
          ],
          groups: [{key: 'g1', label: '封装槽位1'}],
        },
      }),
    ]);

    const name = screen.getByLabelText('封装名称');
    await user.clear(name);
    await user.type(name, '三视图套图');
    await user.click(screen.getByRole('button', { name: '返回模板列表' }));

    expect(screen.getByRole('dialog', { name: '还有未保存的修改' })).toBeInTheDocument();
  });

  it('改过名字再点返回：先问一声，不直接丢掉', async () => {
    const user = await enterEditor();

    const name = screen.getByDisplayValue('商品图模板');
    await user.clear(name);
    await user.type(name, '改过的名字');
    await user.click(screen.getByRole('button', { name: '返回模板列表' }));

    expect(screen.getByRole('dialog', { name: '还有未保存的修改' })).toBeInTheDocument();
    // 还留在编辑页 —— 确认之前一个字都不该丢
    expect(screen.getByDisplayValue('改过的名字')).toBeInTheDocument();
  });

  it('三个出口都给了：保存并发布 / 保存草稿 / 直接离开', async () => {
    const user = await enterEditor();

    const name = screen.getByDisplayValue('商品图模板');
    await user.clear(name);
    await user.type(name, '改过的名字');
    await user.click(screen.getByRole('button', { name: '返回模板列表' }));

    const dialog = screen.getByRole('dialog', { name: '还有未保存的修改' });
    expect(within(dialog).getByRole('button', { name: '保存并发布' })).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: '保存草稿后离开' })).toBeInTheDocument();
    expect(
      within(dialog).getByRole('button', { name: '不保存，直接离开' }),
    ).toBeInTheDocument();
  });

  it('选「留在这页」就真的留下，改动还在', async () => {
    const user = await enterEditor();

    const name = screen.getByDisplayValue('商品图模板');
    await user.clear(name);
    await user.type(name, '改过的名字');
    await user.click(screen.getByRole('button', { name: '返回模板列表' }));
    await user.click(screen.getByRole('button', { name: '留在这一页' }));

    expect(screen.queryByRole('dialog', { name: '还有未保存的修改' })).not.toBeInTheDocument();
    expect(screen.getByDisplayValue('改过的名字')).toBeInTheDocument();
  });

  it('选「不保存，直接离开」才回列表', async () => {
    const user = await enterEditor();

    const name = screen.getByDisplayValue('商品图模板');
    await user.clear(name);
    await user.type(name, '改过的名字');
    await user.click(screen.getByRole('button', { name: '返回模板列表' }));
    await user.click(screen.getByRole('button', { name: '不保存，直接离开' }));

    expect(screen.getByRole('button', { name: '新建模板' })).toBeInTheDocument();
  });

  it('「保存草稿后离开」会真的发保存请求，然后才走', async () => {
    const user = await enterEditor();

    const name = screen.getByDisplayValue('商品图模板');
    await user.clear(name);
    await user.type(name, '改过的名字');
    await user.click(screen.getByRole('button', { name: '返回模板列表' }));

    mutations.save.mockClear();
    await user.click(screen.getByRole('button', { name: '保存草稿后离开' }));

    expect(mutations.save).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.getByRole('button', { name: '新建模板' })).toBeInTheDocument());
  });

  it('保存失败就**不走**：内容还在，错误贴在吸顶栏', async () => {
    const user = await enterEditor();

    const name = screen.getByDisplayValue('商品图模板');
    await user.clear(name);
    await user.type(name, '改过的名字');
    await user.click(screen.getByRole('button', { name: '返回模板列表' }));

    mutations.save.mockRejectedValueOnce(new Error('后端拒了'));
    await user.click(screen.getByRole('button', { name: '保存草稿后离开' }));

    expect(await screen.findByText('后端拒了')).toBeInTheDocument();
    // 还在编辑页，改的名字还在
    expect(screen.getByDisplayValue('改过的名字')).toBeInTheDocument();
  });

  /* 用户报的正是这条：「切换其他页面」时也没提示。
     走的是另一条路（react-router 的 `useBlocker`），只挡返回按钮挡不住它。 */
  it('改过内容后切到别的页面：拦下来并问一声', async () => {
    const user = userEvent.setup();
    const { router } = renderWorkspace([specOf({ status: 'draft' })]);

    await user.click(cardOf('商品图模板').getByRole('button', { name: '编辑' }));
    const name = screen.getByDisplayValue('商品图模板');
    await user.clear(name);
    await user.type(name, '改过的名字');

    void router.navigate('/elsewhere');

    await waitFor(() => {
      expect(screen.getByRole('dialog', { name: '还有未保存的修改' })).toBeInTheDocument();
    });
    // 没跳走
    expect(screen.queryByText('别的页面')).not.toBeInTheDocument();
  });

  /* 关标签 / 刷新只能给浏览器原生提示（文案不可自定义），
     但必须真的挂上监听 —— 否则「切页面有提示、关标签没有」，
     用户会以为已经安全了。 */
  it('有未保存改动时挂上 beforeunload，没改动时不挂', async () => {
    const user = await enterEditor();

    const beforeUnload = () => new Event('beforeunload', { cancelable: true });

    // 刚进编辑页、什么都没改：不该拦
    let event = beforeUnload();
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(false);

    const name = screen.getByDisplayValue('商品图模板');
    await user.clear(name);
    await user.type(name, '改过的名字');

    event = beforeUnload();
    window.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(true);
  });

  /* 「点着玩、什么都没改」的典型路径：把绑定切到「已固定」再切回来。
     `SlotCard` 切回「用户填」时会写 `assetId: null`，而 `blankSlot()` 不带
     这个键 —— 比 JSON 形状就会永远判成「有改动」，明明一个字都没改。 */
  it('切到「已固定」再切回「用户填」：内容没变，不该弹确认', async () => {
    const user = await enterEditor();

    await user.click(screen.getByRole('button', { name: '已固定' }));
    await user.click(screen.getByRole('button', { name: '用户填' }));
    await user.click(screen.getByRole('button', { name: '返回模板列表' }));

    expect(screen.queryByRole('dialog', { name: '还有未保存的修改' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '新建模板' })).toBeInTheDocument();
  });
});

/**
 * 案例入口按权限收敛。
 *
 * 上传 / 选图 / 选视频三个入口都会改案例池，后端一律要 `library:case:write`。
 * 界面不收敛的话，没权限的人点一次吃一次 403 —— 「有这个按钮但按了必失败」
 * 比没有按钮更糟。
 */
describe('模板封装 · 案例入口按权限收敛', () => {
  const enterEditor = async () => {
    const user = userEvent.setup();
    renderWorkspace([specOf({ status: 'draft' })]);
    await user.click(cardOf('商品图模板').getByRole('button', { name: '编辑' }));
    return user;
  };

  it('有 library:case:write 才摆「设置案例」', async () => {
    canState.caseWrite = true;
    await enterEditor();
    expect(screen.getByRole('button', { name: /设置案例/ })).toBeInTheDocument();
  });

  it('没有 library:case:write 就不出现', async () => {
    canState.caseWrite = false;
    await enterEditor();
    expect(screen.queryByRole('button', { name: /设置案例/ })).not.toBeInTheDocument();
  });
});

/**
 * 封装容器：一次选包、多格一起填。
 *
 * 作者的口径是「封装也是一种槽位，可以在它内部放别的槽位」。界面上要能看到
 * 一个框 + 里面几行；而模板库那边只有**一个**选包入口（这条在模板库的用例里守）。
 */
describe('模板封装 · 加封装容器', () => {
  it('加封装 → 一个容器 + 两个图片子槽位', async () => {
    const user = userEvent.setup();
    renderWorkspace([specOf({ status: 'draft' })]);
    await user.click(cardOf('商品图模板').getByRole('button', { name: '编辑' }));

    await user.click(screen.getByRole('button', { name: /加槽位/ }));
    await user.click(screen.getByRole('menuitem', { name: /封装/ }));

    /* 容器标题现在是一个**可改名**的输入框（以前是写死的「封装槽位」），
       所以按无障碍名找，不按文本找 —— 按文本找会随默认名一起变。 */
    const header = screen.getByLabelText('封装名称');
    const box = header.closest('div.rounded-xl');
    expect(box).not.toBeNull();
    // 新建时就把默认名**写进草稿**（不是等作者改一次才显示）：按段序「封装槽位1」
    expect(header).toHaveValue('封装槽位1');
    // 默认两个子槽位（左图/右图这类一对是最常见的用法）
    expect(
      within(box as HTMLElement).getAllByRole('button', { name: '展开更多设置' }),
    ).toHaveLength(2);
  });

  /* 名字是**作者可见的数据**，要能存下来（模板库那边按它显示）。
     只改界面文案、不把它放进提交体，作者改完保存后以为生效了，回到列表还是
     原来的名字 —— 这类「改了但没存」只有靠断言提交体才抓得到。 */
  it('容器名可改，改名随草稿一起提交', async () => {
    const user = userEvent.setup();
    renderWorkspace([specOf({ status: 'draft' })]);
    await user.click(cardOf('商品图模板').getByRole('button', { name: '编辑' }));

    await user.click(screen.getByRole('button', { name: /加槽位/ }));
    await user.click(screen.getByRole('menuitem', { name: /封装/ }));

    const name = screen.getByLabelText('封装名称');
    await user.clear(name);
    await user.type(name, '三视图套图');

    mutations.save.mockClear();
    await user.click(screen.getByRole('button', { name: '保存草稿' }));

    const sent = mutations.save.mock.calls[0][0];
    expect(sent.groups).toEqual([{ key: 'g1', label: '三视图套图' }]);
    // 名字要挂在成员上（组内相邻），否则发布时它写不进任何一行
    expect((sent.slots ?? []).filter(slot => slot.group === 'g1')).toHaveLength(2);
  });

  it('删掉整个容器时，名字一起清掉', async () => {
    const user = userEvent.setup();
    renderWorkspace([specOf({ status: 'draft' })]);
    await user.click(cardOf('商品图模板').getByRole('button', { name: '编辑' }));

    await user.click(screen.getByRole('button', { name: /加槽位/ }));
    await user.click(screen.getByRole('menuitem', { name: /封装/ }));
    await user.click(
      screen.getByRole('button', { name: '删除整个封装槽位（连同里面的槽位）' }),
    );

    mutations.save.mockClear();
    await user.click(screen.getByRole('button', { name: '保存草稿' }));

    /* 容器没了，名字不能还留在 `groups` 里：发布时它写不进快照（没有成员行），
       但草稿指纹会因此与后端不一致 —— 下次保存会平白多一次修订。 */
    expect(mutations.save.mock.calls[0][0].groups).toEqual([]);
  });
});

/**
 * 拖动可以穿过封装的边界。
 *
 * 以前跨容器拖动是**直接 return** 的（怕破坏「成员必须相邻」这条不变量）。
 * 代价是「把外层这一格放进封装」和「把封装里这一格拿出来」都没有手势 ——
 * 只能删掉重建，而删掉会连带清掉提示词里对它的引用。
 *
 * 相邻性其实由落点规则自己保证：**插到谁旁边，就归谁的组**。所以两个方向
 * 是同一个动作的两侧，不需要两套手势。
 */
describe('模板封装 · 拖动槽位进出封装', () => {
  const imageSlot = (key: string, label: string, extra: Record<string, unknown> = {}) => ({
    key,
    type: 'image' as const,
    binding: 'user' as const,
    minCount: 1,
    maxCount: 1,
    label,
    ...extra,
  });

  const baseDraft = (): TemplateSpecIn => ({
    key: 'spec-1',
    name: '商品图模板',
    model: 'bailian-wan3-prime-replacement',
    prompt: '把{{Mixed 1}}和{{Mixed 2}}左右拼接为一张图',
    slots: [
      imageSlot('left', '左图', {group: 'g1', position: 0}),
      imageSlot('right', '右图', {group: 'g1', position: 1}),
      imageSlot('loose', '补充图', {position: 2}),
      imageSlot('extra', '第五张', {position: 3}),
    ],
  });

  /**
   * jsdom 里所有元素的 `getBoundingClientRect()` 都是 0 —— 半区判定会永远落到
   * 「后面」，`before` 那条路根本走不到。给目标行造一个 40px 高、从 0 开始的
   * 真实框，拖动才有上半 / 下半之分。
   */
  function rowOf(key: string) {
    const row = document.querySelector(`[data-slot-key="${key}"]`);
    if (!row) throw new Error(`找不到槽位 ${key} 的行`);
    vi.spyOn(row, 'getBoundingClientRect').mockReturnValue({
      top: 0,
      bottom: 40,
      height: 40,
      left: 0,
      right: 100,
      width: 100,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    } as DOMRect);
    return row;
  }

  function handleOf(key: string) {
    const handle = document.querySelector(
      `[data-slot-key="${key}"] [draggable="true"]`,
    );
    if (!handle) throw new Error(`找不到槽位 ${key} 的拖动手柄`);
    return handle;
  }

  /**
   * jsdom 里**没有 `DragEvent`**，`fireEvent.dragOver(el, {clientY})` 会退化成
   * 一个普通 `Event`，`clientY` 是 undefined —— 半区判定于是永远落到「后面」，
   * 上半区那条路根本走不到（第一版用例就是这么假绿的）。自己造 `MouseEvent`，
   * 坐标才带得进去。
   */
  function fireDrag(el: Element, type: 'dragover' | 'drop', clientY: number) {
    fireEvent(el, new MouseEvent(type, {bubbles: true, cancelable: true, clientY}));
  }

  /** 给封装框造一个 200px 高的真实框（jsdom 里全是 0，上下半判定会失效）。 */
  function boxOf(group: string) {
    const box = document.querySelector(`[data-slot-group="${group}"]`);
    if (!box) throw new Error(`找不到封装 ${group}`);
    vi.spyOn(box, 'getBoundingClientRect').mockReturnValue({
      top: 0, bottom: 200, height: 200, left: 0, right: 300, width: 300, x: 0, y: 0,
      toJSON: () => ({}),
    } as DOMRect);
    return box;
  }

  /** 拖 `from` 到封装框的**空白处**：`top` = 标题栏，`bottom` = 底部按钮那一圈。 */
  function dragToBox(from: string, group: string, half: 'top' | 'bottom') {
    fireEvent.dragStart(handleOf(from));
    const box = boxOf(group);
    const clientY = half === 'top' ? 20 : 180;
    fireDrag(box, 'dragover', clientY);
    fireDrag(box, 'drop', clientY);
  }

  /** 拖 `from` 到 `to` 这一行的上半 / 下半区然后松手。 */
  function drag(from: string, to: string, edge: 'before' | 'after') {
    const clientY = edge === 'before' ? 5 : 35;
    fireEvent.dragStart(handleOf(from));
    const row = rowOf(to);
    fireDrag(row, 'dragover', clientY);
    fireDrag(row, 'drop', clientY);
  }

  async function enter(draft: TemplateSpecIn = baseDraft()) {
    const user = userEvent.setup();
    renderWorkspace([specOf({status: 'draft', draft})]);
    await user.click(cardOf('商品图模板').getByRole('button', {name: '编辑'}));
    mutations.save.mockClear();
    return user;
  }

  async function saveDraft(user: ReturnType<typeof userEvent.setup>) {
    await user.click(screen.getByRole('button', {name: '保存草稿'}));
    return mutations.save.mock.calls[0][0] as TemplateSpecIn;
  }

  it('把外层槽位拖进封装：归到那个组、来源改成封装包', async () => {
    const user = await enter();

    // 「第五张」拖到「右图」上半区 → 插到右图前面，成为容器成员
    drag('extra', 'right', 'before');
    const sent = await saveDraft(user);

    expect(sent.slots!.map(slot => slot.key)).toEqual(['left', 'extra', 'right', 'loose']);
    const moved = sent.slots!.find(slot => slot.key === 'extra')!;
    // 归属改成容器 = 模板库那一格从「列散图」变成「列包」，不需要另一个字段
    expect(moved.group).toBe('g1');
  });

  it('把封装里的槽位拖出来：组清空，来源保留', async () => {
    const user = await enter();

    // 「左图」拖到「补充图」上半区 → 落在容器外面
    drag('left', 'loose', 'before');
    const sent = await saveDraft(user);

    expect(sent.slots!.map(slot => slot.key)).toEqual(['right', 'left', 'loose', 'extra']);
    const moved = sent.slots!.find(slot => slot.key === 'left')!;
    expect(moved.group ?? '').toBe('');
    // 出容器 = 回到「从候选素材里挑」，这也是判据本身，没有第二个字段要跟着改
    expect(moved.key).toBe('left');
  });

  it('容器内换序不动归属：还是那个组（也就是还从包取）', async () => {
    const user = await enter();

    // 「左图」拖到「右图」下半区 —— 同一个容器里换个位置
    drag('left', 'right', 'after');
    const sent = await saveDraft(user);

    expect(sent.slots!.map(slot => slot.key)).toEqual(['right', 'left', 'loose', 'extra']);
    const moved = sent.slots!.find(slot => slot.key === 'left')!;
    expect(moved.group).toBe('g1');
  });

  it('拖到封装框的底部按钮那一圈 ＝ 追加到容器末尾', async () => {
    const user = await enter();

    /* 行自己会 `stopPropagation`，所以能落到框上的只有「框的空白处」——
       两头：标题栏和底部。底部这一带没有精确位置可言，语义就是「丢进这个
       封装」（落最后一个成员后面）。 */
    fireEvent.dragStart(handleOf('extra'));
    const box = boxOf('g1');
    fireDrag(box, 'dragover', 180);
    expect(box.className).toContain('ring-2');
    fireDrag(box, 'drop', 180);

    const sent = await saveDraft(user);
    expect(sent.slots!.map(slot => slot.key)).toEqual(['left', 'right', 'extra', 'loose']);
    expect(sent.slots!.find(slot => slot.key === 'extra')!.group).toBe('g1');
  });

  it('拖到封装框的标题栏 ＝ 插到这个封装**前面**（顶层，不进容器）', async () => {
    /* 回归：以前整块框都算「追加到末尾」，容器排第一时「插到它前面」没有任何
       落点 —— 它上面没有行可以让你拖到「某一行的下半区」。 */
    const user = await enter();

    fireEvent.dragStart(handleOf('extra'));
    const box = boxOf('g1');
    fireDrag(box, 'dragover', 20);
    // 拖的过程中画的是**框上方那条线**，不是整框高亮 —— 后者会被读成「放进这个封装」
    expect(document.querySelector('[data-drop-line="before"]')).not.toBeNull();
    expect(box.className).not.toContain('ring-2');
    fireDrag(box, 'drop', 20);

    const sent = await saveDraft(user);

    expect(sent.slots!.map(slot => slot.key)).toEqual(['extra', 'left', 'right', 'loose']);
    // 插在容器前面 ≠ 加入容器
    expect(sent.slots!.find(slot => slot.key === 'extra')!.group ?? '').toBe('');
  });

  it('把容器里的成员拖到标题栏 ＝ 移出到容器前面', async () => {
    const user = await enter();

    dragToBox('left', 'g1', 'top');
    const sent = await saveDraft(user);

    expect(sent.slots!.map(slot => slot.key)).toEqual(['left', 'right', 'loose', 'extra']);
    expect(sent.slots!.find(slot => slot.key === 'left')!.group ?? '').toBe('');
  });

  it('固定槽位拖不进封装（会把钉住的那张静默丢掉）', async () => {
    const draft = baseDraft();
    draft.slots!.push(
      imageSlot('pinned', '固定图', {binding: 'fixed', assetId: 'a-pinned', position: 4}),
    );
    const user = await enter(draft);

    drag('pinned', 'right', 'after');
    // 拦下来并说清出路，而不是拖完再被后端 400
    expect(
      useToastStore.getState().items.some(item => item.message.includes('先改成「用户填」')),
    ).toBe(true);

    // 结构一点没动
    expect(document.querySelector('[data-slot-key="pinned"]')).not.toBeNull();
    expect(document.querySelector('[data-slot-group="g1"]')?.textContent).not.toContain('固定图');
    void user;
  });

  it('文本槽位拖不进封装（不参与编号分发）', async () => {
    const draft = baseDraft();
    draft.slots!.push({
      key: 'copy',
      type: 'text',
      binding: 'user',
      refMode: 'attachment',
      minCount: 0,
      maxCount: 1,
      label: '文案',
      position: 4,
    });
    await enter(draft);

    drag('copy', 'right', 'after');

    expect(
      useToastStore.getState().items.some(item => item.message.includes('只能放图片或视频')),
    ).toBe(true);
    expect(document.querySelector('[data-slot-group="g1"]')?.textContent).not.toContain('文案');
  });

  it('拖动时把「将要插到的位置」画出来（上半 = 前面，下半 = 后面）', async () => {
    await enter();

    fireEvent.dragStart(handleOf('extra'));
    const row = rowOf('right');

    fireDrag(row, 'dragover', 5);
    expect(document.querySelector('[data-drop-line="before"]')).not.toBeNull();
    expect(document.querySelector('[data-drop-line="after"]')).toBeNull();
    // 行自己 stopPropagation：落在一行上不该让整块封装都亮起来
    expect(
      (document.querySelector('[data-slot-group="g1"]') as HTMLElement).className,
    ).not.toContain('ring-2');

    fireDrag(row, 'dragover', 35);
    expect(document.querySelector('[data-drop-line="after"]')).not.toBeNull();
    expect(document.querySelector('[data-drop-line="before"]')).toBeNull();

    // 拖到自己身上不画线：那里没有「插到自己前面」这回事
    fireDrag(rowOf('extra'), 'dragover', 5);
    expect(document.querySelector('[data-drop-line]')).toBeNull();
  });
});

/**
 * 整个封装也能拖。
 *
 * 不止一个封装时，「谁在前」是作者要表达的（模板库按槽位顺序编号，
 * 封装的先后影响 `{{Mixed N}}`）。整块搬而不是逐格拖：成员必须相邻，
 * 一格一格搬中间会出现「容器断成两截」的瞬时状态。
 */
describe('模板封装 · 拖动整个封装', () => {
  const imageSlot = (key: string, label: string, extra: Record<string, unknown> = {}) => ({
    key,
    type: 'image' as const,
    binding: 'user' as const,
    minCount: 1,
    maxCount: 1,
    label,
    ...extra,
  });

  const draft = (): TemplateSpecIn => ({
    key: 'spec-1',
    name: '商品图模板',
    model: 'bailian-wan3-prime-replacement',
    prompt: '把{{Mixed 1}}和{{Mixed 2}}左右拼接为一张图',
    slots: [
      imageSlot('left', '左图', {group: 'g1', position: 0}),
      imageSlot('right', '右图', {group: 'g1', position: 1}),
      imageSlot('loose', '补充图', {position: 2}),
      imageSlot('extra', '第五张', {position: 3}),
    ],
  });

  function rowOf(key: string) {
    const row = document.querySelector(`[data-slot-key="${key}"]`);
    if (!row) throw new Error(`找不到槽位 ${key} 的行`);
    vi.spyOn(row, 'getBoundingClientRect').mockReturnValue({
      top: 0, bottom: 40, height: 40, left: 0, right: 100, width: 100, x: 0, y: 0,
      toJSON: () => ({}),
    } as DOMRect);
    return row;
  }

  function fireDrag(el: Element, type: 'dragover' | 'drop', clientY: number) {
    fireEvent(el, new MouseEvent(type, {bubbles: true, cancelable: true, clientY}));
  }

  /** 拖整个封装：抓框头那个柄。 */
  function dragGroup(group: string, toKey: string, edge: 'before' | 'after') {
    const handle = document.querySelector(`[data-group-handle="${group}"]`)!;
    fireEvent.dragStart(handle);
    const row = rowOf(toKey);
    const clientY = edge === 'before' ? 5 : 35;
    fireDrag(row, 'dragover', clientY);
    fireDrag(row, 'drop', clientY);
  }

  async function enter() {
    const user = userEvent.setup();
    renderWorkspace([specOf({status: 'draft', draft: draft()})]);
    await user.click(cardOf('商品图模板').getByRole('button', {name: '编辑'}));
    mutations.save.mockClear();
    return user;
  }

  async function saveDraft(user: ReturnType<typeof userEvent.setup>) {
    await user.click(screen.getByRole('button', {name: '保存草稿'}));
    return mutations.save.mock.calls[0][0] as TemplateSpecIn;
  }

  it('整段挪到某个槽位后面：成员顺序与归属都不变', async () => {
    const user = await enter();

    // 容器（左图/右图）拖到「补充图」下半区 → 落到它后面
    dragGroup('g1', 'loose', 'after');
    const sent = await saveDraft(user);

    expect(sent.slots!.map(slot => slot.key)).toEqual(['loose', 'left', 'right', 'extra']);
    expect(sent.slots!.filter(slot => slot.group === 'g1').map(slot => slot.key)).toEqual([
      'left',
      'right',
    ]);
  });

  it('拖到自己的成员行上：画的是「段」的边界，不是行内那条缝', async () => {
    /* 容器里的行不接「整个封装」这个落点 —— 它会往外冒泡，由「这一段」处理。
       否则把容器拖到自己某个成员上，高亮会画在成员之间（看起来像要插进去），
       而实际上它本来就在里面。 */
    await enter();

    const handle = document.querySelector('[data-group-handle="g1"]')!;
    fireEvent.dragStart(handle);
    const row = rowOf('right');

    fireDrag(row, 'dragover', 5);
    expect(document.querySelector('[data-drop-scope="group"]')).not.toBeNull();
    expect(document.querySelector('[data-drop-scope="row"]')).toBeNull();
  });

  it('拖动时画的是「段与段之间」那条缝，不是行内位置', async () => {
    await enter();

    const handle = document.querySelector('[data-group-handle="g1"]')!;
    fireEvent.dragStart(handle);
    const row = rowOf('loose');

    fireDrag(row, 'dragover', 5);
    expect(document.querySelector('[data-drop-line="before"]')).not.toBeNull();
    expect(document.querySelector('[data-drop-line="after"]')).toBeNull();
  });
});

describe('模板封装 · 删除前先问一句', () => {
  /* 断言「还没发请求」要看调用次数，所以每个用例从干净的 mock 开始。 */
  beforeEach(() => {
    vi.clearAllMocks();
  });

  /* 删除是**方向不可逆**的动作（虽然能恢复），所以必须有一层确认。
     确认框是页面内的 `ConfirmDialog`：原生 `window.confirm` 样式不受控，
     还会把整页卡住 —— 用户看不清这次到底会发生什么。 */
  it('点「删除」先弹确认框，还没确认就不发请求', async () => {
    const user = userEvent.setup();
    renderWorkspace([specOf({ status: 'draft' })]);

    await user.click(cardOf('商品图模板').getByRole('button', { name: '删除' }));

    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('移进回收站？');
    expect(dialog).toHaveTextContent('商品图模板');
    expect(mutations.remove).not.toHaveBeenCalled();
  });

  it('取消：什么都不发生', async () => {
    const user = userEvent.setup();
    renderWorkspace([specOf({ status: 'draft' })]);

    await user.click(cardOf('商品图模板').getByRole('button', { name: '删除' }));
    await user.click(await screen.findByRole('button', { name: '取消' }));

    expect(mutations.remove).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('确认：才发删除请求，并给一条成功提示', async () => {
    const user = userEvent.setup();
    renderWorkspace([specOf({ status: 'draft' })]);

    await user.click(cardOf('商品图模板').getByRole('button', { name: '删除' }));
    await user.click(await screen.findByRole('button', { name: '移进回收站' }));

    await waitFor(() => expect(mutations.remove).toHaveBeenCalledWith('spec-1'));
    expect(await screen.findByText('已移进回收站：商品图模板')).toBeInTheDocument();
  });

  it('列表查询失败时展示错误态，而不是显示「还没有模板」', async () => {
    listState.isError = true;
    listState.error = new Error('服务未启动');
    try {
      renderWorkspace([]);
      expect(await screen.findByText('模板列表加载失败')).toBeInTheDocument();
      expect(screen.queryByText('还没有模板')).toBeNull();
    } finally {
      listState.isError = false;
      listState.error = null;
    }
  });
});
