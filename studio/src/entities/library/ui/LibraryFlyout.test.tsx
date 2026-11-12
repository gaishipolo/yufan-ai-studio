import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { LibraryAssetOut, LibraryTagStatOut } from '@/shared/api/schema';
import { LibraryFlyout } from './LibraryFlyout';

/**
 * 悬浮栏的两件事在别处没有守卫：**四个类别**（哪些可选、默认选谁）与
 * **两级标签**（一级点了带一级 id、点了二级就换二级 id）。
 * 这里把查询 hook 换成记录参数的假实现 —— 断言直接落在「发给后端的条件」上，
 * 比去猜 DOM 里选中了哪个胶囊更接近契约。
 */

const { selectionCalls, tagsData, itemsData, tagScopeCalls } = vi.hoisted(() => ({
  selectionCalls: [] as {
    kind?: string;
    tagIds?: string[];
    keyword?: string;
  }[],
  tagsData: { current: [] as LibraryTagStatOut[] },
  itemsData: { current: [] as LibraryAssetOut[] },
  tagScopeCalls: [] as { state?: string; library?: string; kind?: string }[],
}));

vi.mock('../api/library.queries', () => ({
  useLibraryTags: (scope: { state?: string; library?: string; kind?: string }) => {
    tagScopeCalls.push(scope);
    return { data: tagsData.current };
  },
  useLibrarySelectionInfinite: (params: { kind?: string; tagIds?: string[]; keyword?: string }) => {
    selectionCalls.push(params);
    return {
      data: { pages: [{ items: itemsData.current, total: itemsData.current.length }] },
      isPending: false,
      isError: false,
      hasNextPage: false,
      isFetchingNextPage: false,
      fetchNextPage: vi.fn(),
      refetch: vi.fn(),
    };
  },
}));

function tag(
  id: string,
  name: string,
  extra: Partial<LibraryTagStatOut> = {},
): LibraryTagStatOut {
  return { id, name, createdAt: `2026-11-0${id === 'p1' ? 1 : 2}T00:00:00Z`, ...extra };
}

/** 候选卡片：形状取后端 `LibraryAssetOut` 的必要字段 */
function assetOf(id: string, name: string): LibraryAssetOut {
  return {
    id,
    name,
    kind: 'image',
    origin: 'upload',
    ownerId: 'u-1',
    visibility: 'personal',
    state: 'active',
    createdAt: '2026-11-01T00:00:00Z',
  };
}

/** 产品（一级）旗下 3030 / 3020；场景（一级）无子标签 —— 两种形态都要覆盖 */
const TAGS: LibraryTagStatOut[] = [
  tag('p1', '产品', { assetCount: 9, childCount: 2 }),
  tag('c1', '3030', { assetCount: 4, parentId: 'p1', parentName: '产品' }),
  tag('c2', '3020', { assetCount: 5, parentId: 'p1', parentName: '产品' }),
  tag('p2', '场景', { assetCount: 3, childCount: 0 }),
];

const renderFlyout = (kinds?: ('image' | 'video' | 'audio' | 'text')[]) =>
  render(
    <LibraryFlyout
      onClose={vi.fn()}
      max={3}
      pickedIds={[]}
      onPick={vi.fn()}
      {...(kinds ? { kinds } : {})}
    />,
  );

const lastCall = () => selectionCalls[selectionCalls.length - 1];

/** 两行标签各自的胶囊文案会撞（「产品」既是一级也在二级的父名前缀里），按行取 */
const topRow = () => screen.getByRole('group', { name: '一级标签' });
const childRow = () => screen.getByRole('group', { name: '二级标签' });

describe('LibraryFlyout · 四个类别', () => {
  beforeEach(() => {
    selectionCalls.length = 0;
    itemsData.current = [];
    tagsData.current = TAGS;
  });

  it('四个类别都渲染且都能点，默认落在调用方认的第一个类别', () => {
    renderFlyout();
    const group = screen.getByRole('group', { name: '素材类别' });
    const labels = Array.from(group.querySelectorAll('button')).map((b) => b.textContent);
    expect(labels).toEqual(['成片', '图片', '音频', '文本']);

    for (const name of ['成片', '图片', '音频', '文本']) {
      expect(screen.getByRole('button', { name })).toBeEnabled();
    }
    expect(screen.getByRole('button', { name: '图片' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('切到接收方不认的类别：能看，但不能加入', async () => {
    const user = userEvent.setup();
    itemsData.current = [
      { ...assetOf('v1', '一条成片.mp4'), kind: 'video' },
    ];
    renderFlyout();

    await user.click(screen.getByRole('button', { name: '成片' }));
    expect(lastCall().kind).toBe('video');
    // 说明行点名「只能浏览」，并给出本次真正能挑的类别
    expect(screen.getByText(/只能浏览成片素材：本次只收 图片/)).toBeInTheDocument();
    // 卡片在，但右上角的「＋」不给
    expect(screen.getByTitle('一条成片.mp4')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /加入参考图/ })).toBeNull();
  });

  it('默认类别取调用方 kinds 的第一个，并按该类别请求', () => {
    renderFlyout(['text']);
    expect(screen.getByRole('button', { name: '文本' })).toHaveAttribute('aria-pressed', 'true');
    expect(lastCall().kind).toBe('text');
    // 换成文本后，空态文案跟着类别走，不再写死「图片」
    expect(screen.getByText('素材库里还没有文本素材')).toBeInTheDocument();
  });

  it('切换类别会清掉已选标签：上个类别的计数口径不该继续筛', async () => {
    const user = userEvent.setup();
    renderFlyout(['image', 'video']);

    await user.click(within(topRow()).getByRole('button', { name: /^产品/ }));
    expect(lastCall().tagIds).toEqual(['p1']);

    await user.click(screen.getByRole('button', { name: '成片' }));
    expect(lastCall().kind).toBe('video');
    expect(lastCall().tagIds).toBeUndefined();
  });
});

describe('LibraryFlyout · 一级 / 二级标签', () => {
  beforeEach(() => {
    selectionCalls.length = 0;
    itemsData.current = [];
    tagsData.current = TAGS;
  });

  it('默认两行都在：一级列一级，二级列全部子标签并带父名', () => {
    renderFlyout();
    expect(screen.getByRole('group', { name: '一级标签' })).toBeInTheDocument();
    const second = screen.getByRole('group', { name: '二级标签' });
    // 没选一级时两组混排，必须带父名才能分辨
    expect(second).toHaveTextContent('产品 / 3030');
    expect(second).toHaveTextContent('产品 / 3020');
  });

  it('点一级把一级 id 发下去，二级行收窄到该组', async () => {
    const user = userEvent.setup();
    renderFlyout();

    await user.click(within(topRow()).getByRole('button', { name: /^产品/ }));
    expect(lastCall().tagIds).toEqual(['p1']);

    const second = childRow();
    expect(second).toHaveTextContent('3030');
    // 已经收窄到「产品」旗下，行内标签不用再带父名
    expect(second).not.toHaveTextContent('产品 / 3030');
  });

  it('点二级换成二级 id（不再带父），一级行仍高亮该组', async () => {
    const user = userEvent.setup();
    renderFlyout();

    await user.click(within(topRow()).getByRole('button', { name: /^产品/ }));
    await user.click(within(childRow()).getByRole('button', { name: /3030/ }));

    expect(lastCall().tagIds).toEqual(['c1']);
    expect(within(topRow()).getByRole('button', { name: /^产品/ })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });

  it('没先选一级，直接从「全部二级」点进去再取消：回到无筛选', async () => {
    const user = userEvent.setup();
    renderFlyout();

    await user.click(within(childRow()).getByRole('button', { name: /3030/ }));
    expect(lastCall().tagIds).toEqual(['c1']);

    await user.click(within(childRow()).getByRole('button', { name: /3030/ }));
    // 一级从没被点过，取消二级就不该凭空变成「整组筛选」
    expect(lastCall().tagIds).toBeUndefined();
  });

  it('先选一级再点二级，取消二级回到整组而不是全库', async () => {
    const user = userEvent.setup();
    renderFlyout();

    await user.click(within(topRow()).getByRole('button', { name: /^产品/ }));
    await user.click(within(childRow()).getByRole('button', { name: /3030/ }));
    await user.click(within(childRow()).getByRole('button', { name: /3030/ }));
    expect(lastCall().tagIds).toEqual(['p1']);
  });

  it('词表里一个二级都没有：二级行退化成一句说明，而不是假装有', () => {
    tagsData.current = [tag('p2', '场景', { assetCount: 3, childCount: 0 })];
    renderFlyout();
    expect(screen.getByRole('group', { name: '一级标签' })).toBeInTheDocument();
    expect(screen.queryByRole('group', { name: '二级标签' })).toBeNull();
    expect(screen.getByText(/还没有二级标签：在资产库「管理标签」里/)).toBeInTheDocument();
  });

  it('选到还没挂系列的一级：二级行留着并说明「该一级下还没有二级标签」', async () => {
    const user = userEvent.setup();
    tagsData.current = [...TAGS, tag('p3', '空组', { assetCount: 0, childCount: 0 })];
    renderFlyout();

    await user.click(within(topRow()).getByRole('button', { name: /^空组/ }));
    // 整行消失会让人以为「又只有一级」，还会让下面内容往上跳
    const second = childRow();
    expect(second).toHaveTextContent('该一级下还没有二级标签');
  });
});

describe('LibraryFlyout · 标签计数按当前类别', () => {
  beforeEach(() => {
    selectionCalls.length = 0;
    tagScopeCalls.length = 0;
    itemsData.current = [];
    tagsData.current = TAGS;
  });

  it('默认按「图片」类别请求标签计数', () => {
    renderFlyout();
    expect(tagScopeCalls.at(-1)).toMatchObject({
      state: 'active',
      library: 'all',
      kind: 'image',
    });
  });

  it('切到别的类别，标签计数跟着换口径', async () => {
    const user = userEvent.setup();
    renderFlyout(['image', 'text']);

    await user.click(screen.getByRole('button', { name: '文本' }));
    expect(tagScopeCalls.at(-1)).toMatchObject({
      state: 'active',
      library: 'all',
      kind: 'text',
    });
  });

  it('胶囊上的数字就是当前类别这一档的条数', () => {
    // 后端按 kind 返回的计数直接显示：全类是 9，图片这一档是 4
    tagsData.current = [tag('p1', '产品', { assetCount: 4, childCount: 0 })];
    renderFlyout();
    expect(within(topRow()).getByRole('button', { name: /^产品/ })).toHaveTextContent('4');
  });
});
