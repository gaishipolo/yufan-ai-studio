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
}: {
  base: SpecSlotIn;
  onPatch: (patch: Partial<SpecSlotIn>) => void;
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
    />
  );
}

function renderSlot(slot: Partial<SpecSlotIn> = {}) {
  const onPatch = vi.fn();
  const base: SpecSlotIn = {
    key: 'image_1',
    type: 'image',
    binding: 'user',
    refMode: 'mixed',
    minCount: 1,
    maxCount: 1,
    label: '',
    usage: '',
    position: 0,
    ...slot,
  };
  const client = new QueryClient({
    defaultOptions: {queries: {retry: false, enabled: false}},
  });
  const view = render(
    <QueryClientProvider client={client}>
      <SlotHarness base={base} onPatch={onPatch} />
    </QueryClientProvider>,
  );
  return {onPatch, ...view};
}

describe('槽位卡片', () => {
  it('默认只露出高频项：类型、最少/最多、用途说明都收着', () => {
    renderSlot();
    // 类型标记可见（一眼知道要什么）—— 现在是图标，名称留给读屏
    expect(screen.getByRole('img', {name: '图片'})).toBeInTheDocument();
    // 低频项默认不渲染
    expect(screen.queryByLabelText('类型')).not.toBeInTheDocument();
    expect(screen.queryByText('最少')).not.toBeInTheDocument();
    expect(screen.queryByText('进提示词的用途说明')).not.toBeInTheDocument();
  });

  it('点「更多设置」才展开低频项', async () => {
    const user = userEvent.setup();
    renderSlot();
    await user.click(screen.getByRole('button', {name: '展开更多设置'}));

    expect(screen.getByLabelText('类型')).toBeInTheDocument();
    expect(screen.getByText('最少')).toBeInTheDocument();
    expect(screen.getByText('最多')).toBeInTheDocument();
    expect(screen.getByText('进提示词的用途说明')).toBeInTheDocument();
    // 展开后按钮变成「收起」
    expect(screen.getByRole('button', {name: '收起更多设置'})).toBeInTheDocument();
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
});
