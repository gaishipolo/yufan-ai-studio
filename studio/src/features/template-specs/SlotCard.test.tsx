import { useState } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';
import type { SpecSlotIn } from '@/shared/api/schema';
import { SlotCard } from './SlotCard';

/* AssetChip 会去查资产详情（拿名字和封面）。这里不打真后端 ——
   测的是槽位卡片的布局与交互，素材本身长什么样不归它管。 */
vi.mock('@/entities/library/ui/AssetChip', () => ({
  AssetChip: () => <span>素材</span>,
}));

const TAG_OPTIONS = [
  {value: 'tag-1', label: '产品'},
  {value: 'tag-2', label: '产品 / 3030'},
];

const TYPE_OPTIONS = [
  {value: 'image', label: '图片'},
  {value: 'video', label: '成片'},
];

/** 槽位卡片是**受控**组件：值由上层持有。测试里用一个有状态的外壳，
 *  这样「勾一个再勾一个」才是真的累加，而不是每次都从空值开始。 */
function SlotHarness({
  base,
  onPatch,
  prompt,
  inGroup,
}: {
  base: SpecSlotIn;
  onPatch: (patch: Partial<SpecSlotIn>) => void;
  prompt: string;
  inGroup?: boolean;
}) {
  const [current, setCurrent] = useState<SpecSlotIn>(base);
  const patch = (next: Partial<SpecSlotIn>) => {
    onPatch(next);
    setCurrent(previous => ({...previous, ...next}));
  };
  return (
    <SlotCard
      slot={current}
      index={0}
      inGroup={inGroup}
      dragging={false}
      onDragStart={() => {}}
      onDragEnd={() => {}}
      onPatch={patch}
      onDelete={() => {}}
      onPickAsset={() => {}}
      tagOptions={TAG_OPTIONS}
      typeOptions={TYPE_OPTIONS}
      slots={[current]}
      prompt={prompt}
    />
  );
}

/**
 * 默认提示词：**引用了当前槽位**。
 *
 * 两段文案（选中时 / 未选时）是「替换 `{{slot:key}}` 插入点」的文案 ——
 * 没有插入点就不显示（填了也不进提示词，是死数据）。
 */
const DEFAULT_PROMPT = '把 {{Mixed 1}} 放进画面';

function renderSlot(
  slot: Partial<SpecSlotIn> = {},
  prompt = DEFAULT_PROMPT,
  extra: {inGroup?: boolean} = {},
) {
  const onPatch = vi.fn();
  const base: SpecSlotIn = {
    key: 'image_1',
    type: 'image',
    binding: 'user',
    refMode: 'mixed',
    minCount: 1,
    maxCount: 1,
    label: '',
    position: 0,
    ...slot,
  };
  const client = new QueryClient({
    defaultOptions: {queries: {retry: false, enabled: false}},
  });
  const view = render(
    <QueryClientProvider client={client}>
      <SlotHarness base={base} onPatch={onPatch} prompt={prompt} {...extra} />
    </QueryClientProvider>,
  );
  return {onPatch, ...view};
}

describe('槽位卡片', () => {
  it('类型常驻在主行下方，不用展开就能改', () => {
    renderSlot();
    // 主行的类型图标 + 下方那行的类型下拉都在（同一个字段两种呈现）
    expect(screen.getByRole('img', {name: '图片'})).toBeInTheDocument();
    expect(screen.getByRole('combobox', {name: '类型'})).toBeInTheDocument();
  });

  it('点「更多设置」展开的是低频项', async () => {
    const user = userEvent.setup();
    renderSlot();
    await user.click(screen.getByRole('button', {name: '展开更多设置'}));

    // 数量框已移除：一槽固定一张，必填/可选由主行的开关表达，不再有「最少/最多」。
    expect(screen.queryByText('最少')).not.toBeInTheDocument();
    expect(screen.queryByText('最多')).not.toBeInTheDocument();
    // 展开后按钮变成「收起」
    expect(screen.getByRole('button', {name: '收起更多设置'})).toBeInTheDocument();
  });

  it('「进提示词的用途说明」没了（它从来没进过提示词）', async () => {
    const user = userEvent.setup();
    renderSlot();
    await user.click(screen.getByRole('button', {name: '展开更多设置'}));

    expect(screen.queryByText('进提示词的用途说明')).not.toBeInTheDocument();
  });

  it('「谁定」用两个按钮切换，不用下拉', async () => {
    const user = userEvent.setup();
    const {onPatch} = renderSlot({binding: 'user'});

    expect(screen.getByRole('button', {name: '用户填'})).toHaveAttribute('aria-pressed', 'true');
    await user.click(screen.getByRole('button', {name: '已固定'}));
    expect(onPatch).toHaveBeenCalledWith(expect.objectContaining({binding: 'fixed'}));
  });

  it('切回「用户填」时清掉预置的素材（两边互斥）', async () => {
    const user = userEvent.setup();
    const {onPatch} = renderSlot({binding: 'fixed', assetId: 'asset-1'});

    await user.click(screen.getByRole('button', {name: '用户填'}));
    // 用户填的槽位不能预置 assetId，后端会 400
    expect(onPatch).toHaveBeenCalledWith(
      expect.objectContaining({binding: 'user', assetId: null}),
    );
  });

  /** 候选标签移到主行下方常驻：不用点「展开更多设置」就能看到并操作。
      它是发布闸门，藏在展开区里会让作者一路配完才发现发布被 400 打回。 */
  it('候选标签常驻在主行下方，无需展开更多设置', () => {
    renderSlot({type: 'image', binding: 'user', tagIds: []});
    expect(screen.getByRole('button', {name: '候选标签'})).toBeInTheDocument();
    expect(screen.getByRole('button', {name: '展开更多设置'})).toHaveAttribute('aria-expanded', 'false');
  });

  it('缺标签时给出醒目提示（发布闸门）', () => {
    renderSlot({type: 'image', binding: 'user', tagIds: []});
    expect(screen.getByText(/缺标签/)).toBeInTheDocument();
  });

  it('固定槽位不要求标签', () => {
    renderSlot({type: 'image', binding: 'fixed', tagIds: []});
    expect(screen.queryByText(/缺标签/)).not.toBeInTheDocument();
  });

  it('候选标签能多选：勾一个再勾一个，两个都交回去', async () => {
    const user = userEvent.setup();
    const {onPatch} = renderSlot({type: 'image', binding: 'user', tagIds: []});

    await user.click(screen.getByRole('button', {name: '候选标签'}));
    await user.click(screen.getByRole('checkbox', {name: '产品'}));
    expect(onPatch).toHaveBeenCalledWith(expect.objectContaining({tagIds: ['tag-1']}));

    // 再勾一个 → 两个都交回去（多个标签之间是并集）
    await user.click(screen.getByRole('checkbox', {name: '产品 / 3030'}));
    expect(onPatch).toHaveBeenCalledWith(
      expect.objectContaining({tagIds: ['tag-1', 'tag-2']}),
    );
  });

  it('固定槽位显示已选素材与「更换」按钮', () => {
    renderSlot({type: 'image', binding: 'fixed', assetId: 'asset-1'});
    expect(screen.getByRole('button', {name: '更换'})).toBeInTheDocument();
  });

  it('固定槽位还没选素材时按钮是「选择素材」（没东西可换）', () => {
    renderSlot({type: 'image', binding: 'fixed', assetId: ''});
    expect(screen.getByRole('button', {name: '选择素材'})).toBeInTheDocument();
  });

  it('展示名是可编辑输入框', async () => {
    const user = userEvent.setup();
    const {onPatch} = renderSlot({label: '产品图'});
    const input = screen.getByLabelText('展示名');
    expect(input).toHaveValue('产品图');

    await user.type(input, 'X');
    expect(onPatch).toHaveBeenCalledWith(expect.objectContaining({label: '产品图X'}));
  });

  it('「必填 / 可选」只在用户填的槽位上出现', () => {
    const {unmount} = renderSlot({binding: 'user'});
    expect(screen.getByRole('button', {name: '必填'})).toBeInTheDocument();
    expect(screen.getByRole('button', {name: '可选'})).toBeInTheDocument();
    unmount();

    renderSlot({binding: 'fixed'});
    expect(screen.queryByRole('button', {name: '可选'})).not.toBeInTheDocument();
  });

  it('默认（minCount=1）是「必填」档', () => {
    renderSlot({binding: 'user', minCount: 1});
    expect(screen.getByRole('button', {name: '必填'})).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', {name: '可选'})).toHaveAttribute('aria-pressed', 'false');
  });

  it('切到「可选」把 minCount 归零（可选 = minCount 0，不另存 required）', async () => {
    const user = userEvent.setup();
    const {onPatch} = renderSlot({binding: 'user', minCount: 1, maxCount: 1});

    await user.click(screen.getByRole('button', {name: '可选'}));
    expect(onPatch).toHaveBeenCalledWith(
      expect.objectContaining({minCount: 0, maxCount: 1}),
    );
    // 不能顺手引入第二个事实源
    expect(onPatch.mock.calls[0][0]).not.toHaveProperty('required');
  });

  it('切回「必填」把 minCount 拉回至少 1', async () => {
    const user = userEvent.setup();
    const {onPatch} = renderSlot({binding: 'user', minCount: 0, maxCount: 2});

    await user.click(screen.getByRole('button', {name: '必填'}));
    expect(onPatch).toHaveBeenCalledWith(
      expect.objectContaining({minCount: 1, maxCount: 1}),
    );
  });

  /* 「选中时文案」只对**可选**槽位有意义：必填 = 素材一定在 = 直接插编号，
     没有「填了 / 没填」两种状态。 */
  it('「选中时文案」只在可选槽位上出现（必填槽位直接不渲染）', async () => {
    const user = userEvent.setup();
    const {unmount} = renderSlot({binding: 'user', minCount: 0});
    await user.click(screen.getByRole('button', {name: '展开更多设置'}));
    expect(screen.getByLabelText('选中时文案')).toBeInTheDocument();
    unmount();

    renderSlot({binding: 'user', minCount: 1});
    await user.click(screen.getByRole('button', {name: '展开更多设置'}));
    expect(screen.queryByLabelText('选中时文案')).not.toBeInTheDocument();
  });

  it('「选中时文案」输入能回传 filledText', async () => {
    const user = userEvent.setup();
    const {onPatch} = renderSlot({binding: 'user', minCount: 0});

    await user.click(screen.getByRole('button', {name: '展开更多设置'}));
    const area = screen.getByLabelText('选中时文案');
    await user.type(area, '人物参考');
    expect(onPatch).toHaveBeenCalledWith(
      expect.objectContaining({filledText: '人物参考'}),
    );
  });

  /* 「未选时文案」整块都不在槽位卡片里（2026-11-25 用户定）：
     它只有一个真相来源 —— 提示词正文里那段高亮文字。
     先前留了只读展示块，用户反馈「未选时文案还在呢」—— 看得见就还是
     两份，作者会以为在卡片里也能改。彻底拿掉。 */
  it('槽位卡片里完全没有「未选时文案」（无论可选 / 必填 / 是否设过）', async () => {
    const user = userEvent.setup();
    renderSlot({binding: 'user', minCount: 0, emptyText: '一望无际的雪原'});
    await user.click(screen.getByRole('button', {name: '展开更多设置'}));

    expect(screen.queryByText('未选时文案')).not.toBeInTheDocument();
    expect(screen.queryByRole('textbox', {name: '未选时文案'})).not.toBeInTheDocument();
    /* 已设的那段也不在卡片里重复展示 */
    expect(screen.queryByText('一望无际的雪原')).not.toBeInTheDocument();
  });

  /* 「选中时文案」的门槛只有一条：**槽位是可选的**（2026-11-18 用户拍板：
     「不需要引用这个槽位，只要是可选就显示」）。

     之前还有一条「提示词里必须有单张引用」—— 那是在拿**写作顺序**
     当门槛：作者得先跳到正文插一个 token，再回来写话。写作顺序不该
     由界面的门槛决定。

     没有插入点时这段话**编译期用不上**，所以界面照实说一句
     （见下一条用例），而不是让人白填一场还不自知。 */
  it('提示词里没引用这个槽位时，可选槽位仍然显示「选中时文案」', async () => {
    const user = userEvent.setup();
    renderSlot({binding: 'user', minCount: 0}, '把别的图放进画面');
    await user.click(screen.getByRole('button', {name: '展开更多设置'}));

    expect(screen.getByLabelText('选中时文案')).toBeInTheDocument();
  });

  it('提示词里没引用时，照实说这段话还用不上（写在占位提示里，不占版面）', async () => {
    const user = userEvent.setup();
    renderSlot({binding: 'user', minCount: 0}, '把别的图放进画面');
    await user.click(screen.getByRole('button', {name: '展开更多设置'}));

    /* 说明放在框内的**占位提示**里，不是常驻小字 ——
       提示跟着框走、填了内容就自动消失、不占额外版面。 */
    const filled = screen.getByLabelText('选中时文案');
    expect(filled).toHaveAttribute(
      'placeholder',
      expect.stringContaining('写下 {{Mixed 1}} 引用这个槽位，这段话才会被用上'),
    );
  });

  it('整组引用 {{slot:x*}} 同样显示两段文案（可选就显示）', async () => {
    const user = userEvent.setup();
    renderSlot({binding: 'user', minCount: 0}, '把 {{Mixed 1*}} 都放进去');
    await user.click(screen.getByRole('button', {name: '展开更多设置'}));

    expect(screen.getByLabelText('选中时文案')).toBeInTheDocument();
  });

  it('第 N 张引用 {{slot:x.2}} 同样显示两段文案', async () => {
    const user = userEvent.setup();
    renderSlot({binding: 'user', minCount: 0}, '把 {{Mixed 1.2}} 放进去');
    await user.click(screen.getByRole('button', {name: '展开更多设置'}));

    expect(screen.getByLabelText('选中时文案')).toBeInTheDocument();
  });

  it('必填槽位：不渲染这两个框，也不摆说明小字', async () => {
    /* 必填 = 素材一定在 = 直接插编号，没有「没填」这个状态。
       **不摆说明小字** —— 「没有这两格」本身就是解释，
       再加一句「改成可选才能写」就是占版面。 */
    const user = userEvent.setup();
    renderSlot({binding: 'user', minCount: 1});
    await user.click(screen.getByRole('button', {name: '展开更多设置'}));

    expect(screen.queryByLabelText('选中时文案')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('未选时文案')).not.toBeInTheDocument();
    expect(screen.queryByText(/必填槽位一定有素材/)).not.toBeInTheDocument();
  });

  it('可选槽位（有插入点）两段话都在', async () => {
    const user = userEvent.setup();
    renderSlot({binding: 'user', minCount: 0});
    await user.click(screen.getByRole('button', {name: '展开更多设置'}));

    expect(screen.getByLabelText('选中时文案')).toBeInTheDocument();
    /* 未选时文案不在卡片里，唯一入口是正文的高亮段 */
    expect(screen.queryByText('未选时文案')).not.toBeInTheDocument();
  });

  /* 占位符不再写死 token —— 改成打 `@` 选，候选里是**当前槽位自己的**引用。
     写死 `{{slot:image_1}}` 的话，第二个槽位给的就是第一个槽位的 token ——
     作者照着抄进提示词，引用到的还是那个槽位自己，编号会错位。

     候选上显示的是**展示名**（「图片 1」），不是内部 key —— key 是给编译器看的，
     作者看不懂。token 才是真的（下面那条用例验它）。 */
  it('「选中时文案」打 @ 的候选是当前槽位自己的', async () => {
    const user = userEvent.setup();
    renderSlot(
      {key: 'image_7', binding: 'user', minCount: 0, label: '主图'},
      '把 {{slot:image_7}} 放进去',
    );
    await user.click(screen.getByRole('button', {name: '展开更多设置'}));

    const area = screen.getByLabelText('选中时文案');
    await user.click(area);
    await user.type(area, '@');

    // 候选按展示名显示，不暴露内部 key
    expect(screen.getByRole('menuitem', {name: /主图（单张）/})).toBeInTheDocument();
    expect(screen.queryByRole('button', {name: /image_7/})).not.toBeInTheDocument();
  });

  it('「选中时文案」打 @ 选候选，插入的是自己 token', async () => {
    const user = userEvent.setup();
    const {onPatch} = renderSlot({key: 'image_7', binding: 'user', minCount: 0}, '把 {{slot:image_7}} 放进去');
    await user.click(screen.getByRole('button', {name: '展开更多设置'}));

    const area = screen.getByLabelText('选中时文案');
    await user.click(area);
    await user.type(area, '人物参考 @');
    await user.click(screen.getByRole('menuitem', {name: /单张/}));

    expect(onPatch).toHaveBeenCalledWith(
      expect.objectContaining({filledText: '人物参考 {{Mixed 1}}'}),
    );
  });
});

/**
 * 「来源」：素材 / 封装包两档。
 *
 * 它改的不是界面样式，而是**模板库那一格能挑什么**：声明为包的槽位只列包，
 * 素材槽位不列包。所以它得在封装页看得见、改得动，且不能留下后端会拒的组合
 * （固定槽位 + 从包取、文本/音频 + 从包取）。
 */
/**
 * 槽位卡片里**没有「来源」这一项**。
 *
 * 2026-11-20 定死：封装容器里必然来自封装包，容器外必然来自素材 —— 来源是
 * `group` 的结果，不是作者要声明的东西。这一列在任何槽位上都该消失；
 * 留着就是一个「作者看不见、却还在影响模板库列什么」的隐藏状态。
 */
describe('槽位卡片 · 没有「来源」这一项', () => {
  it('独立槽位：找不到', () => {
    renderSlot();

    expect(screen.getByRole('combobox', {name: '类型'})).toBeInTheDocument();
    expect(screen.queryByLabelText('来源')).not.toBeInTheDocument();
  });

  it('容器里的槽位：同样找不到', () => {
    renderSlot({group: 'g1'}, DEFAULT_PROMPT, {inGroup: true});

    expect(screen.getByRole('combobox', {name: '类型'})).toBeInTheDocument();
    expect(screen.queryByLabelText('来源')).not.toBeInTheDocument();
  });
});

describe('槽位卡片 · 在封装容器里', () => {
  it('不给「用户填 / 已固定」：容器里的槽位只能由使用者填', () => {
    renderSlot({group: 'g1'}, DEFAULT_PROMPT, {inGroup: true});

    expect(screen.queryByRole('button', {name: /已固定/})).not.toBeInTheDocument();
    // 必填/可选还在（它决定使用者能不能跳过这一格）
    expect(screen.getByRole('button', {name: /必填/})).toBeInTheDocument();
  });
});
