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
}: {
  base: SpecSlotIn;
  onPatch: (patch: Partial<SpecSlotIn>) => void;
  prompt: string;
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
      dragging={false}
      onDragStart={() => {}}
      onDragEnd={() => {}}
      onDragOver={() => {}}
      onDrop={() => {}}
      onPatch={patch}
      onDelete={() => {}}
      onPickAsset={() => {}}
      tagOptions={TAG_OPTIONS}
      typeOptions={TYPE_OPTIONS}
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
const DEFAULT_PROMPT = '把 {{slot:image_1}} 放进画面';

function renderSlot(slot: Partial<SpecSlotIn> = {}, prompt = DEFAULT_PROMPT) {
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
      <SlotHarness base={base} onPatch={onPatch} prompt={prompt} />
    </QueryClientProvider>,
  );
  return {onPatch, ...view};
}

describe('槽位卡片', () => {
  it('默认只露出高频项：类型、最少/最多都收着', () => {
    renderSlot();
    // 类型标记可见（一眼知道要什么）—— 现在是图标，名称留给读屏
    expect(screen.getByRole('img', {name: '图片'})).toBeInTheDocument();
    // 低频项默认不渲染
    expect(screen.queryByLabelText('类型')).not.toBeInTheDocument();
    expect(screen.queryByText('最少')).not.toBeInTheDocument();
  });

  it('点「更多设置」才展开低频项', async () => {
    const user = userEvent.setup();
    renderSlot();
    await user.click(screen.getByRole('button', {name: '展开更多设置'}));

    expect(screen.getByLabelText('类型')).toBeInTheDocument();
    expect(screen.getByText('最少')).toBeInTheDocument();
    expect(screen.getByText('最多')).toBeInTheDocument();
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

  it('缺标签时主行给出醒目提示（发布闸门）', () => {
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

    await user.click(screen.getByRole('button', {name: '展开更多设置'}));
    // 多选下拉的收起态：FormField 的 label 指向它，所以可访问名是「候选标签」
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
      expect.objectContaining({minCount: 1, maxCount: 2}),
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

  /* 「未选时文案」只在**可选**槽位上出现（必填槽位不填素材会直接报错，
     用不上这段文案）。以前是「显示但禁用」—— 一个永远不能用的框，
     摆在那里只会让人以为填了有用。 */
  it('「未选时文案」只在可选槽位上出现（必填槽位直接不渲染）', async () => {
    const user = userEvent.setup();
    const {unmount} = renderSlot({binding: 'user', minCount: 0});
    await user.click(screen.getByRole('button', {name: '展开更多设置'}));
    expect(screen.getByLabelText('未选时文案')).toBeInTheDocument();
    unmount();

    renderSlot({binding: 'user', minCount: 1});
    await user.click(screen.getByRole('button', {name: '展开更多设置'}));
    expect(screen.queryByLabelText('未选时文案')).not.toBeInTheDocument();
  });

  it('「未选时文案」输入能回传 emptyText', async () => {
    const user = userEvent.setup();
    const {onPatch} = renderSlot({binding: 'user', minCount: 0});
    await user.click(screen.getByRole('button', {name: '展开更多设置'}));

    await user.type(screen.getByLabelText('未选时文案'), '红裙');
    expect(onPatch).toHaveBeenCalledWith(expect.objectContaining({emptyText: '红裙'}));
  });

  it('两段文案默认收在「更多设置」里', () => {
    renderSlot({binding: 'user'});
    expect(screen.queryByLabelText('选中时文案')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('未选时文案')).not.toBeInTheDocument();
  });

  it('固定槽位没有两段文案（素材早定死，不存在「没素材」）', async () => {
    const user = userEvent.setup();
    renderSlot({binding: 'fixed'});

    await user.click(screen.getByRole('button', {name: '展开更多设置'}));
    expect(screen.queryByLabelText('选中时文案')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('未选时文案')).not.toBeInTheDocument();
  });

  /* 两段文案的门槛只有一条：**槽位是可选的**（2026-11-18 用户拍板：
     「不需要引用这个槽位，只要是可选就显示两个输入框」）。

     之前还有一条「提示词里必须有单张引用」—— 那是在拿**写作顺序**
     当门槛：作者得先跳到正文插一个 token，再回来写话。写作顺序不该
     由界面的门槛决定。

     没有插入点时这两段话**编译期用不上**，所以界面照实说一句
     （见下一条用例），而不是让人白填一场还不自知。 */
  it('提示词里没引用这个槽位时，可选槽位仍然显示两段文案', async () => {
    const user = userEvent.setup();
    renderSlot({binding: 'user', minCount: 0}, '把别的图放进画面');
    await user.click(screen.getByRole('button', {name: '展开更多设置'}));

    expect(screen.getByLabelText('选中时文案')).toBeInTheDocument();
    expect(screen.getByLabelText('未选时文案')).toBeInTheDocument();
  });

  it('提示词里没引用时，照实说这两段话还用不上（写在占位提示里，不占版面）', async () => {
    const user = userEvent.setup();
    renderSlot({binding: 'user', minCount: 0}, '把别的图放进画面');
    await user.click(screen.getByRole('button', {name: '展开更多设置'}));

    /* 说明放在框内的**占位提示**里，不是常驻小字 ——
       提示跟着框走、填了内容就自动消失、不占额外版面。 */
    const filled = screen.getByLabelText('选中时文案');
    const empty = screen.getByLabelText('未选时文案');
    expect(filled).toHaveAttribute(
      'placeholder',
      expect.stringContaining('写下 {{slot:image_1}} 引用这个槽位，这段话才会被用上'),
    );
    expect(empty).toHaveAttribute(
      'placeholder',
      expect.stringContaining('写下 {{slot:image_1}} 引用这个槽位，这段话才会被用上'),
    );
    /* 两个框的占位提示必须能分开 —— 文字一样的话使用者分不清
       哪个框是「有素材」、哪个是「没素材」。 */
    expect(filled.getAttribute('placeholder')).not.toBe(empty.getAttribute('placeholder'));
  });

  it('整组引用 {{slot:x*}} 同样显示两段文案（可选就显示）', async () => {
    const user = userEvent.setup();
    renderSlot({binding: 'user', minCount: 0}, '把 {{slot:image_1*}} 都放进去');
    await user.click(screen.getByRole('button', {name: '展开更多设置'}));

    expect(screen.getByLabelText('选中时文案')).toBeInTheDocument();
  });

  it('第 N 张引用 {{slot:x.2}} 同样显示两段文案', async () => {
    const user = userEvent.setup();
    renderSlot({binding: 'user', minCount: 0}, '把 {{slot:image_1.2}} 放进去');
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
    expect(screen.getByLabelText('未选时文案')).toBeInTheDocument();
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
    expect(screen.getByRole('button', {name: /主图（单张）/})).toBeInTheDocument();
    expect(screen.queryByRole('button', {name: /image_7/})).not.toBeInTheDocument();
  });

  it('「选中时文案」打 @ 选候选，插入的是自己 token', async () => {
    const user = userEvent.setup();
    const {onPatch} = renderSlot({key: 'image_7', binding: 'user', minCount: 0}, '把 {{slot:image_7}} 放进去');
    await user.click(screen.getByRole('button', {name: '展开更多设置'}));

    const area = screen.getByLabelText('选中时文案');
    await user.click(area);
    await user.type(area, '人物参考 @');
    await user.click(screen.getByRole('button', {name: /单张/}));

    expect(onPatch).toHaveBeenCalledWith(
      expect.objectContaining({filledText: '人物参考 {{slot:image_7}}'}),
    );
  });
});
