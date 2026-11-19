import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { LibraryTagStatOut } from '@/shared/api/schema';
import { AssetPicker } from './AssetPicker';

/**
 * 素材选择器（封装页与模板库共用）：
 *
 * - 候选集是**槽位的标签**（作者的）—— 那圈定了素材范围；
 * - 界面上只列**这批素材身上实际有**的标签，用户点了就在范围里再筛；
 * - 使用者的挑按维度**替换**候选集里那一维，不是叠加。
 *
 * 断言落在「发给后端的 tagIds」上 —— 筛选语义真正发生的地方。
 */

const { selectionCalls, tagsData, selectionItems, canState } = vi.hoisted(() => ({
  selectionCalls: [] as { kind?: string; tagIds?: string[]; purpose?: string }[],
  tagsData: { current: [] as LibraryTagStatOut[] },
  selectionItems: { current: [] as unknown[] },
  canState: { current: true },
}));

/* 权限也是输入条件：「案例池」那一档只在有 `library:case:read` 时出现。 */
vi.mock('@/shared/access/use-access', () => ({
  useCan: () => canState.current,
}));

vi.mock('@/entities/library/api/library.queries', () => ({
  useLibraryTags: () => ({ data: tagsData.current }),
}));

vi.mock('@/entities/library/api/library.api', () => ({
  libraryApi: {
    selection: (params: { kind?: string; tagIds?: string[]; purpose?: string }) => {
      selectionCalls.push(params);
      return Promise.resolve({
        items: selectionItems.current,
        total: selectionItems.current.length,
        page: 1,
        pageSize: 24,
      });
    },
  },
}));

/** 两级：一级是维度（产品 / 角色），二级是取值（3030 / 菜菜）。 */
const TAGS: LibraryTagStatOut[] = [
  { id: 't1', name: '产品', assetCount: 7, childCount: 2, createdAt: '2026-11-09T10:01:00Z' },
  { id: 't2', name: '角色', assetCount: 3, childCount: 1, createdAt: '2026-11-09T10:02:00Z' },
  { id: 't11', name: '3030', parentId: 't1', parentName: '产品', depth: 2, assetCount: 5, childCount: 0, createdAt: '2026-11-09T10:04:00Z' },
  { id: 't21', name: '菜菜', parentId: 't2', parentName: '角色', depth: 2, assetCount: 3, childCount: 0, createdAt: '2026-11-09T10:06:00Z' },
];

beforeEach(() => {
  selectionCalls.length = 0;
  tagsData.current = TAGS;
  selectionItems.current = [];
  canState.current = true;
});

function renderPicker(
  tagIds: string[],
  kind: 'image' | 'video' | 'audio' | 'text' = 'image',
  extra: {casePool?: boolean; title?: string} = {},
) {
  const client = new QueryClient({defaultOptions: {queries: {retry: false}}});
  return render(
    <QueryClientProvider client={client}>
      <AssetPicker
        open
        kind={kind}
        tagIds={tagIds}
        selected={[]}
        onClose={vi.fn()}
        onToggle={vi.fn()}
        {...extra}
      />
    </QueryClientProvider>,
  );
}

describe('AssetPicker · 候选集里再筛', () => {
  it('没挑标签时按槽位的候选集筛', async () => {
    renderPicker(['t1']);
    expect(selectionCalls.at(-1)?.tagIds).toEqual(['t1']);
  });

  it('维度行不显示（那只是分组头，范围已由候选集定死）', async () => {
    renderPicker(['t1']);
    expect(screen.queryByRole('group', {name: '维度标签'})).not.toBeInTheDocument();
  });

  it('只列这批素材身上实际有的取值（计数为 0 的不出现）', async () => {
    /* 词表里「菜菜」没有素材 → 不该出现，用户点了只会得到空列表。 */
    tagsData.current = TAGS.map((tag) =>
      tag.id === 't21' ? { ...tag, assetCount: 0 } : tag,
    );
    renderPicker(['t1']);
    const second = within(screen.getByRole('group', {name: '取值标签'}));
    expect(second.queryByRole('button', {name: '菜菜'})).not.toBeInTheDocument();
    expect(second.getByRole('button', {name: '3030'})).toBeInTheDocument();
  });

  it('候选集里的标签不列出来（它就是范围，点了等于没筛）', async () => {
    renderPicker(['t11']);
    const second = within(screen.getByRole('group', {name: '取值标签'}));
    expect(second.queryByRole('button', {name: '3030'})).not.toBeInTheDocument();
    expect(second.getByRole('button', {name: '菜菜'})).toBeInTheDocument();
  });

  it('同一维度：使用者的挑替换候选集里那一维', async () => {
    const user = userEvent.setup();
    renderPicker(['t1']);
    const second = within(screen.getByRole('group', {name: '取值标签'}));
    await user.click(second.getByRole('button', {name: '3030'}));
    expect(selectionCalls.at(-1)?.tagIds).toEqual(['t11']);
  });

  it('跨维度：候选集那一维保留，使用者挑的加进来', async () => {
    const user = userEvent.setup();
    renderPicker(['t1']);
    const second = within(screen.getByRole('group', {name: '取值标签'}));
    await user.click(second.getByRole('button', {name: '菜菜'}));
    expect(selectionCalls.at(-1)?.tagIds).toEqual(['t1', 't21']);
  });

  it('单选：再点另一个取值就是**替换**，不是追加', async () => {
    /* 回归：多选时逐个覆盖会让多个胶囊全高亮、却只筛出最后一档。
       单选下只剩后点的那一个（候选集那一维被替换掉）。 */
    const user = userEvent.setup();
    tagsData.current = [
      ...TAGS,
      { id: 't12', name: 'T40', parentId: 't1', parentName: '产品', depth: 2, assetCount: 2, childCount: 0, createdAt: '2026-11-09T10:05:00Z' },
    ];
    renderPicker(['t1']);
    const second = within(screen.getByRole('group', {name: '取值标签'}));
    await user.click(second.getByRole('button', {name: '3030'}));
    await user.click(second.getByRole('button', {name: 'T40'}));
    expect(selectionCalls.at(-1)?.tagIds).toEqual(['t12']);
  });

  it('没圈标签的槽位也能按任意取值筛', async () => {
    const user = userEvent.setup();
    renderPicker([]);
    const second = within(screen.getByRole('group', {name: '取值标签'}));
    await user.click(second.getByRole('button', {name: '菜菜'}));
    expect(selectionCalls.at(-1)?.tagIds).toEqual(['t21']);
  });
});

/**
 * 文本素材没有缩略图。
 *
 * 后端对 `kind=text` 的 `/thumbnail` 直接 404（「文本资产没有缩略图」）。
 * 素材库里另外三处（`LibraryCard` / `LibraryFlyout` / `LibraryPicker`）
 * 都用 `textExcerpt` 画文字卡 —— 这里原本是四份渲染里唯一漏掉的一处：
 * 所有类型一律画 `CoverImage`，于是选文本时满屏渐变占位，
 * 看不出选的是哪段文字。
 */
describe('AssetPicker · 文本素材', () => {
  const TEXT_ASSET = {
    id: 'txt-1',
    name: '雨夜开场文案.txt',
    kind: 'text',
    origin: 'upload',
    ownerId: 'u-1',
    visibility: 'personal',
    state: 'active',
    createdAt: '2026-11-01T00:00:00Z',
    coverUrl: null,
    textExcerpt: '第一段：雨夜街头，霓虹灯牌。\n第二段：镜头缓缓推近。',
  };

  it('用摘要画文字卡，不画缩略图', async () => {
    selectionItems.current = [TEXT_ASSET];
    renderPicker([], 'text');

    expect(await screen.findByText(/雨夜街头/)).toBeInTheDocument();
    // 不该去请求注定 404 的缩略图
    expect(document.querySelector('img[src*="thumbnail"]')).toBeNull();
  });

  it('摘要为空时有个占位说法，不留一块空白', async () => {
    selectionItems.current = [{ ...TEXT_ASSET, textExcerpt: null }];
    renderPicker([], 'text');

    expect(await screen.findByText('（空文本）')).toBeInTheDocument();
  });

  it('图片素材照旧画缩略图（别把两种渲染混起来）', async () => {
    selectionItems.current = [
      {
        ...TEXT_ASSET,
        id: 'img-1',
        kind: 'image',
        coverUrl: '/cover/img-1.png',
        textExcerpt: null,
      },
    ];
    renderPicker([], 'image');

    await screen.findByText('雨夜开场文案.txt');
    expect(screen.queryByText('（空文本）')).not.toBeInTheDocument();
    expect(document.querySelector('img[src="/cover/img-1.png"]')).not.toBeNull();
  });
});

/**
 * 案例池：设置展示案例时的第二档。
 *
 * 案例不进普通列表、选择器默认也把它排掉 —— 少了这一档，作者传过的案例
 * 第二次就挑不到了（只能用一次）。所以这里守三件事：默认不发 `purpose`、
 * 切到案例池才发、没开这一档时压根没有这个开关。
 */
describe('AssetPicker · 案例池', () => {
  it('默认按普通素材口径取（不传 purpose）', () => {
    renderPicker([]);
    expect(selectionCalls.at(-1)?.purpose).toBeUndefined();
    expect(screen.queryByRole('button', {name: '案例池'})).not.toBeInTheDocument();
  });

  it('切到案例池才带 purpose=case，切回来就去掉', async () => {
    const user = userEvent.setup();
    renderPicker([], 'image', {casePool: true});
    await user.click(screen.getByRole('button', {name: '案例池'}));
    expect(selectionCalls.at(-1)?.purpose).toBe('case');
    await user.click(screen.getByRole('button', {name: '我的素材'}));
    expect(selectionCalls.at(-1)?.purpose).toBeUndefined();
  });

  it('标题可以按场景换（设置展示案例时不能还写「选择参考图」）', () => {
    renderPicker([], 'image', {casePool: true, title: '选择展示案例'});
    expect(screen.getByText('选择展示案例')).toBeInTheDocument();
    expect(screen.queryByText('选择参考图')).not.toBeInTheDocument();
  });

  it('没有 library:case:read 就不摆这一档（后端会 403）', () => {
    canState.current = false;
    renderPicker([], 'image', {casePool: true});
    expect(screen.queryByRole('button', {name: '案例池'})).not.toBeInTheDocument();
  });

  it('案例池空时的说明指向「转成案例」，不是「去素材库上传」', async () => {
    const user = userEvent.setup();
    renderPicker([], 'image', {casePool: true});
    await user.click(screen.getByRole('button', {name: '案例池'}));
    expect(await screen.findByText(/挑一条转成案例/)).toBeInTheDocument();
  });
});
