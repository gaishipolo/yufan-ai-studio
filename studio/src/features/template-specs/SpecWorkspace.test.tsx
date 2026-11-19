import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
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
    save: { mutateAsync: vi.fn(), isPending: false },
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
  return render(
    <QueryClientProvider client={client}>
      <SpecWorkspace />
    </QueryClientProvider>,
  );
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
