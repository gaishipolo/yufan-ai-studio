import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { LibraryTagStatOut, SpecSlotOut } from '@/shared/api/schema';
import { SlotMediaPicker } from './SlotMediaPicker';

/**
 * 模板库填表页的槽位选择器：
 *
 * - 候选集是**槽位的标签**（作者圈的）—— 那圈定了素材范围；
 * - 界面上只列**这批素材身上实际有**的取值，一级维度行不显示；
 * - 候选集里的标签本身也不列 —— 它就是范围，点了等于没筛；
 * - 使用者的挑按维度**替换**候选集里那一维，不是叠加。
 *
 * 断言落在「发给后端的 tagIds」上 —— 那才是筛选语义真正发生的地方。
 */

const { selectionCalls, tagsData, itemsData, packsData, packCalls } = vi.hoisted(() => ({
  selectionCalls: [] as { kind?: string; tagIds?: string[]; pageSize?: number }[],
  tagsData: { current: [] as LibraryTagStatOut[] },
  itemsData: { current: [] as { id: string; name: string; kind: string; coverUrl?: string }[] },
  packsData: { current: [] as { id: string; name: string; coverUrl?: string; memberCount?: number }[] },
  packCalls: [] as { tagIds?: string[] }[],
}));

vi.mock('@/entities/library/api/library.queries', () => ({
  useLibraryTags: () => ({ data: tagsData.current, isPending: false }),
  /* 槽位网格里还会列封装包；这些用例不关心，给空列表 */
  /* 包预览浮层用的详情查询（悬停才 enabled，这里不关心内容） */
  useLibraryPack: () => ({data: undefined, isError: false, error: null}),
  /* `enabled` 要照着实现来：没给 onPickPack 时组件不发这次请求（`data` 为 undefined） */
  useLibraryPacks: (params: { tagIds?: string[] }, enabled = true) => {
    packCalls.push(params);
    return { data: enabled ? packsData.current : undefined };
  },
  useLibrarySelection: (params: { kind?: string; tagIds?: string[]; pageSize?: number }) => {
    selectionCalls.push(params);
    /* 按 `pageSize` **真的截断**：不截断的话「还有 N 张」那条永远不出现，
       守卫会变成一句空话。`total` 是这一档标签下的总数。 */
    const size = params.pageSize ?? 24;
    return {
      data: { items: itemsData.current.slice(0, size), total: itemsData.current.length },
      isSuccess: true,
      isLoading: false,
    };
  },
  /* 悬浮大图：这里只关心「有没有被调用」，字节由组件自己的用例验。 */
  useLibraryLargePreview: (assetId: string | null, enabled: boolean) => ({
    url: enabled && assetId ? `blob:${assetId}` : undefined,
    isPending: false,
  }),
}));

/** 两级：一级是维度（产品 / 角色），二级是取值（3030 / 菜菜）。 */
const TAGS: LibraryTagStatOut[] = [
  { id: 't1', name: '产品', assetCount: 7, childCount: 2, createdAt: '2026-11-09T10:01:00Z' },
  { id: 't2', name: '角色', assetCount: 3, childCount: 1, createdAt: '2026-11-09T10:02:00Z' },
  { id: 't11', name: '3030', parentId: 't1', parentName: '产品', depth: 2, assetCount: 5, childCount: 0, createdAt: '2026-11-09T10:04:00Z' },
  { id: 't21', name: '菜菜', parentId: 't2', parentName: '角色', depth: 2, assetCount: 3, childCount: 0, createdAt: '2026-11-09T10:06:00Z' },
];

function slotOf(extra: Partial<SpecSlotOut> = {}): SpecSlotOut {
  return {
    key: 'hero',
    type: 'image',
    binding: 'user',
    refMode: 'mixed',
    minCount: 1,
    maxCount: 1,
    label: '主图',
    position: 1,
    ...extra,
  };
}

beforeEach(() => {
  selectionCalls.length = 0;
  packCalls.length = 0;
  tagsData.current = TAGS;
  itemsData.current = [];
  packsData.current = [];
});

describe('SlotMediaPicker · 候选集里再筛', () => {
  it('没挑标签时按槽位的候选集筛', () => {
    render(
      <SlotMediaPicker slot={slotOf({tagIds: ['t1']})} selected={[]} onChange={vi.fn()} />,
    );
    expect(selectionCalls.at(-1)?.tagIds).toEqual(['t1']);
  });

  it('维度行不显示（那只是分组头，范围已由候选集定死）', () => {
    render(
      <SlotMediaPicker slot={slotOf({tagIds: ['t1']})} selected={[]} onChange={vi.fn()} />,
    );
    expect(screen.queryByRole('group', {name: '维度标签'})).not.toBeInTheDocument();
  });

  it('只列这批素材身上实际有的取值（计数为 0 的不出现）', () => {
    tagsData.current = TAGS.map((tag) =>
      tag.id === 't21' ? { ...tag, assetCount: 0 } : tag,
    );
    render(
      <SlotMediaPicker slot={slotOf({tagIds: ['t1']})} selected={[]} onChange={vi.fn()} />,
    );
    const second = within(screen.getByRole('group', {name: '取值标签'}));
    expect(second.queryByRole('button', {name: '菜菜'})).not.toBeInTheDocument();
    expect(second.getByRole('button', {name: '3030'})).toBeInTheDocument();
  });

  it('候选集里的标签不列出来（它就是范围，点了等于没筛）', () => {
    /* 候选集圈了「产品 / 3030」→ 素材已经是 3030；
       「3030」不该再是一个可点项，但同维度的兄弟值还得能挑。 */
    tagsData.current = [
      ...TAGS,
      { id: 't12', name: 'T40', parentId: 't1', parentName: '产品', depth: 2, assetCount: 2, childCount: 0, createdAt: '2026-11-09T10:05:00Z' },
    ];
    render(
      <SlotMediaPicker slot={slotOf({tagIds: ['t11']})} selected={[]} onChange={vi.fn()} />,
    );
    const second = within(screen.getByRole('group', {name: '取值标签'}));
    expect(second.queryByRole('button', {name: '3030'})).not.toBeInTheDocument();
    expect(second.getByRole('button', {name: 'T40'})).toBeInTheDocument();
  });

  it('同一维度：使用者的挑替换候选集里那一维', async () => {
    const user = userEvent.setup();
    render(
      <SlotMediaPicker slot={slotOf({tagIds: ['t1']})} selected={[]} onChange={vi.fn()} />,
    );
    const second = within(screen.getByRole('group', {name: '取值标签'}));
    await user.click(second.getByRole('button', {name: '3030'}));
    expect(selectionCalls.at(-1)?.tagIds).toEqual(['t11']);
  });

  it('跨维度：候选集那一维保留，使用者挑的加进来', async () => {
    const user = userEvent.setup();
    render(
      <SlotMediaPicker slot={slotOf({tagIds: ['t1']})} selected={[]} onChange={vi.fn()} />,
    );
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
    render(
      <SlotMediaPicker slot={slotOf({tagIds: ['t1']})} selected={[]} onChange={vi.fn()} />,
    );
    const second = within(screen.getByRole('group', {name: '取值标签'}));
    await user.click(second.getByRole('button', {name: '3030'}));
    await user.click(second.getByRole('button', {name: 'T40'}));
    expect(selectionCalls.at(-1)?.tagIds).toEqual(['t12']);
  });

  it('老模板（没圈标签）照样能挑，只是不能按标签收窄', () => {
    render(
      <SlotMediaPicker slot={slotOf({tagIds: []})} selected={[]} onChange={vi.fn()} />,
    );
    expect(screen.getByText(/还没圈候选标签/)).toBeInTheDocument();
    expect(selectionCalls.at(-1)?.tagIds).toBeUndefined();
    // 没有范围时所有取值都能挑
    const second = within(screen.getByRole('group', {name: '取值标签'}));
    expect(second.getByRole('button', {name: '菜菜'})).toBeInTheDocument();
  });
});

describe('SlotMediaPicker · 一屏放不下时', () => {
  const many = (count: number) =>
    Array.from({ length: count }, (_, index) => ({
      id: `a${index}`,
      name: `图${index}.png`,
      kind: 'image',
    }));

  it('先取一屏，说清还有多少张，点一下取回来', async () => {
    /* 回归：之前只取前 24 张、没有下一页，标题却写着「共 30」——
       第 25 张以后在界面上根本不存在。 */
    const user = userEvent.setup();
    itemsData.current = many(30);
    render(
      <SlotMediaPicker slot={slotOf({tagIds: ['t11']})} selected={[]} onChange={vi.fn()} />,
    );
    expect(screen.getAllByTitle(/^图\d+\.png$/)).toHaveLength(24);

    await user.click(screen.getByRole('button', {name: /还有 6 张/}));
    expect(screen.getAllByTitle(/^图\d+\.png$/)).toHaveLength(30);
    expect(selectionCalls.at(-1)?.pageSize).toBe(30);
    expect(screen.queryByRole('button', {name: /还有/})).not.toBeInTheDocument();
  });
});

/**
 * 可选槽位：**不摆**「不填会怎样」的说明。
 *
 * 模板库右栏现在有「随机性」三档：可选槽位要么不出现（均衡档），
 * 要么出现但由系统/使用者挑（随机/自定义）。「不填则用：…」是
 * **填表页的旧形态** —— 同一件事说两遍，而且它描述的状态（不填）
 * 在新形态下已经不成立了。
 */
describe('SlotMediaPicker · 可选槽位', () => {
  it('可选槽位不摆「不填则用」的说明', () => {
    render(
      <SlotMediaPicker
        slot={slotOf({minCount: 0, emptyText: '主角是穿红裙的年轻女性'})}
        selected={[]}
        onChange={vi.fn()}
      />,
    );
    expect(screen.queryByText(/不填/)).not.toBeInTheDocument();
    expect(screen.queryByText(/可选：/)).not.toBeInTheDocument();
  });

  it('必填槽位标星，可选槽位不标星', () => {
    const {unmount} = render(
      <SlotMediaPicker slot={slotOf({minCount: 1})} selected={[]} onChange={vi.fn()} />,
    );
    expect(screen.getByText('*')).toBeInTheDocument();
    unmount();

    render(
      <SlotMediaPicker slot={slotOf({minCount: 0})} selected={[]} onChange={vi.fn()} />,
    );
    expect(screen.queryByText('*')).not.toBeInTheDocument();
  });
});

/**
 * 悬浮大图：网格里的缩略图只有 360×240 的封面，看不清产品细节。
 * 悬停时另浮一张大图，**点一下仍然是选中**。
 */
describe('SlotMediaPicker · 悬浮大图', () => {
  const ITEMS = [
    { id: 'a1', name: 'T35黑色三视图', kind: 'image', coverUrl: 'https://x/1.jpg' },
    { id: 'a2', name: 'T35正面图', kind: 'image', coverUrl: 'https://x/2.jpg' },
  ];

  beforeEach(() => {
    itemsData.current = ITEMS;
  });

  it('悬停缩略图会浮出大图', async () => {
    vi.useFakeTimers();
    try {
      render(
        <SlotMediaPicker slot={slotOf({tagIds: []})} selected={[]} onChange={vi.fn()} />,
      );
      const thumb = screen.getByTitle('T35黑色三视图');
      fireEvent.mouseEnter(thumb, { clientX: 200, clientY: 200 });
      await vi.advanceTimersByTimeAsync(300);
      expect(screen.getByAltText('T35黑色三视图')).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it('点缩略图仍然是选中（浮层不接管点击）', async () => {
    vi.useFakeTimers();
    try {
      const onChange = vi.fn();
      render(
        <SlotMediaPicker slot={slotOf({tagIds: []})} selected={[]} onChange={onChange} />,
      );
      const thumb = screen.getByTitle('T35黑色三视图');
      fireEvent.mouseEnter(thumb, { clientX: 200, clientY: 200 });
      await vi.advanceTimersByTimeAsync(300);
      /* 浮层确实出现了 —— 下面这一步才是「点了还能选中」的对照 */
      expect(screen.getByAltText('T35黑色三视图')).toBeInTheDocument();
      fireEvent.click(thumb);
      expect(onChange).toHaveBeenCalledWith(['a1']);
    } finally {
      vi.useRealTimers();
    }
  });

  it('鼠标移开就撤掉大图', async () => {
    vi.useFakeTimers();
    try {
      render(
        <SlotMediaPicker slot={slotOf({tagIds: []})} selected={[]} onChange={vi.fn()} />,
      );
      const thumb = screen.getByTitle('T35黑色三视图');
      fireEvent.mouseEnter(thumb, { clientX: 200, clientY: 200 });
      await vi.advanceTimersByTimeAsync(300);
      expect(screen.getByAltText('T35黑色三视图')).toBeInTheDocument();
      fireEvent.mouseLeave(thumb);
      expect(screen.queryByAltText('T35黑色三视图')).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('SlotMediaPicker · 封装包', () => {
  /* 包就是一种素材：占同一张网格里的一个格子，不是另一种界面模式。
     区别只在「点它的效果」——一次填好几格（由上层的 onPickPack 做）。 */
  it('包和素材同一个网格，带「N 张」角标', () => {
    packsData.current = [{ id: 'p1', name: '6036 黑', memberCount: 3 }];
    itemsData.current = [{ id: 'a1', name: '散图.png', kind: 'image' }];
    render(
      <SlotMediaPicker
        slot={slotOf({ tagIds: ['t1'] })}
        selected={[]}
        onChange={vi.fn()}
        onPickPack={vi.fn()}
      />,
    );

    expect(screen.getByTitle('6036 黑（封装 · 3 张）')).toBeInTheDocument();
    expect(screen.getByTitle('散图.png')).toBeInTheDocument();
    expect(screen.getByText('3 张')).toBeInTheDocument();
  });

  it('点包把 id 交给上层（分发是它的事，这里不自己填）', async () => {
    const user = userEvent.setup();
    const onPickPack = vi.fn();
    const onChange = vi.fn();
    packsData.current = [{ id: 'p1', name: '6036 黑', memberCount: 3 }];
    render(
      <SlotMediaPicker
        slot={slotOf({ tagIds: ['t1'] })}
        selected={[]}
        onChange={onChange}
        onPickPack={onPickPack}
      />,
    );

    await user.click(screen.getByTitle('6036 黑（封装 · 3 张）'));
    expect(onPickPack).toHaveBeenCalledWith('p1');
    // 点包不等于选中某个素材 id —— 包不是素材行
    expect(onChange).not.toHaveBeenCalled();
  });

  it('没给 onPickPack 就不显示包（文本 / 音频槽位走不到这里）', () => {
    packsData.current = [{ id: 'p1', name: '6036 黑', memberCount: 3 }];
    itemsData.current = [{ id: 'a1', name: '散图.png', kind: 'image' }];
    render(<SlotMediaPicker slot={slotOf({ tagIds: ['t1'] })} selected={[]} onChange={vi.fn()} />);
    expect(screen.queryByTitle('6036 黑（封装 · 3 张）')).not.toBeInTheDocument();
  });

  it('这一格已经填满，包也还能点（再套一次是幂等的）', async () => {
    /* 包是**动作**不是选中项：它改的是「这一批格子」。按本格满了就置灰，
       等于「正面图先手挑了一张」之后，这一行里的包再也点不动 —— 而它本来
       正好能把三视图那一格一起填上。灰掉的样子还像「这个包坏了」。 */
    const user = userEvent.setup();
    packsData.current = [{ id: 'p1', name: '6036 黑', memberCount: 3 }];
    itemsData.current = [{ id: 'a1', name: '散图.png', kind: 'image' }];
    const onPickPack = vi.fn();
    render(
      <SlotMediaPicker
        slot={slotOf({ tagIds: ['t1'] })}
        selected={['a1']}
        onChange={vi.fn()}
        onPickPack={onPickPack}
      />,
    );
    const packButton = screen.getByTitle('6036 黑（封装 · 3 张）');
    expect(packButton).not.toBeDisabled();
    await user.click(packButton);
    expect(onPickPack).toHaveBeenCalledWith('p1');
  });

  it('包按槽位的候选标签过滤（与素材同一套条件）', () => {
    render(
      <SlotMediaPicker
        slot={slotOf({ tagIds: ['t1'] })}
        selected={[]}
        onChange={vi.fn()}
        onPickPack={vi.fn()}
      />,
    );
    expect(packCalls.at(-1)?.tagIds).toEqual(['t1']);
  });

  it('只有包没有素材时不算空态', () => {
    packsData.current = [{ id: 'p1', name: '6036 黑', memberCount: 2 }];
    itemsData.current = [];
    render(
      <SlotMediaPicker
        slot={slotOf({ tagIds: ['t1'] })}
        selected={[]}
        onChange={vi.fn()}
        onPickPack={vi.fn()}
      />,
    );
    expect(screen.queryByText(/还没有图片/)).not.toBeInTheDocument();
    expect(screen.getByTitle('6036 黑（封装 · 2 张）')).toBeInTheDocument();
  });
});
