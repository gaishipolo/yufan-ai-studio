import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { RouterProvider, createMemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import type { TemplateSpecOut } from '@/shared/api/schema';
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

const { specs, trashedSpecs, mutations } = vi.hoisted(() => ({
  specs: { current: [] as TemplateSpecOut[] },
  trashedSpecs: { current: [] as TemplateSpecOut[] },
  mutations: {
    save: vi.fn(() => Promise.resolve({ key: 'spec-1', revision: 2 })),
    publish: vi.fn(() => Promise.resolve({ version: 3, spec: {} })),
    disable: vi.fn(() => Promise.resolve({})),
    restore: vi.fn(() => Promise.resolve({})),
    remove: vi.fn(() => Promise.resolve({})),
  },
}));

vi.mock('@/entities/template-spec/api/spec.queries', () => ({
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
  it('启用成功的提示说「已启用」，不说「已发布 vN」', async () => {
    const user = userEvent.setup();
    renderWorkspace([specOf({ status: 'disabled', currentVersion: 2 })]);

    await user.click(cardOf('商品图模板').getByRole('button', { name: '启用' }));

    expect(await screen.findByText('已启用 商品图模板')).toBeInTheDocument();
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

  it('没有「提示词」这条板块标题', async () => {
    await enterEditor();

    expect(screen.queryByRole('heading', { name: '提示词' })).not.toBeInTheDocument();
  });

  it('提示词输入框本身还在（取消的是外壳，不是框）', async () => {
    await enterEditor();

    expect(screen.getByRole('textbox', { name: '提示词' })).toBeInTheDocument();
  });

  it('不再有「+ 文本素材」这个重复入口（左栏加号菜单已覆盖）', async () => {
    await enterEditor();

    expect(screen.queryByRole('button', { name: /文本素材/ })).not.toBeInTheDocument();
  });

  /* 字数是一门**约束**（超了保存不了），取消板块时不能连带删掉。 */
  it('字数上限还在，而且写法是 N / 上限', async () => {
    await enterEditor();

    expect(screen.getByText(new RegExp(`/\\s*20000`))).toBeInTheDocument();
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
