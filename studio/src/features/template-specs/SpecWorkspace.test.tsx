import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { RouterProvider, createMemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { TemplateSpecOut } from '@/shared/api/schema';
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

const { specs, trashedSpecs, mutations, canState } = vi.hoisted(() => ({
  canState: {caseWrite: true},
  specs: { current: [] as TemplateSpecOut[] },
  trashedSpecs: { current: [] as TemplateSpecOut[] },
  mutations: {
    /* 带参数：有用例要断言「提交上去的提示词是什么」。 */
    save: vi.fn((_payload: { prompt: string }) =>
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
    data: { items: specs.current, total: specs.current.length, page: 1, pageSize: 100 },
    isLoading: false,
    isError: false,
    error: null,
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

    const header = screen.getByText('封装槽位');
    const box = header.closest('div.rounded-xl');
    expect(box).not.toBeNull();
    // 默认两个子槽位（左图/右图这类一对是最常见的用法）
    expect(
      within(box as HTMLElement).getAllByRole('button', { name: '展开更多设置' }),
    ).toHaveLength(2);
  });
});
