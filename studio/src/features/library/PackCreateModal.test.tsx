import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { LibraryAssetOut } from '@/shared/api/schema';
import { PackCreateModal } from './PackCreateModal';

const { createMock, toastMock, pickerProps } = vi.hoisted(() => ({
  createMock: vi.fn(),
  toastMock: vi.fn(),
  pickerProps: { current: {} as Record<string, unknown> },
}));

vi.mock('@/entities/library/api/library.queries', () => ({
  useCreatePack: () => ({ mutateAsync: createMock, isPending: false }),
}));
vi.mock('@/shared/lib/toast', () => ({ toast: toastMock }));

/* 选择器自己有一套查询与分页，这里只关心「它把选中的结果交回来」 */
vi.mock('@/entities/library/ui/LibraryPicker', () => ({
  LibraryPicker: (props: Record<string, unknown>) => {
    pickerProps.current = props;
    if (!props.open) return null;
    const onConfirm = props.onConfirm as (assets: LibraryAssetOut[]) => void;
    return (
      <button
        type="button"
        onClick={() =>
          onConfirm([assetOf('m3', '6036-黑-细节.png'), assetOf('m1', '6036-黑-正面.png')])
        }
      >
        模拟确认选择
      </button>
    );
  },
}));

function assetOf(id: string, name: string, extra: Partial<LibraryAssetOut> = {}): LibraryAssetOut {
  return {
    id,
    name,
    kind: 'image',
    origin: 'upload',
    ownerId: 'u1',
    visibility: 'personal',
    state: 'active',
    sizeBytes: 1024,
    createdAt: '2026-11-19T02:00:00Z',
    coverUrl: `https://cdn.test/${id}.jpg`,
    ...extra,
  };
}

const PRESET = [assetOf('m1', '6036-黑-正面.png'), assetOf('m2', '6036-黑-立体.png')];

function renderModal(overrides: Partial<Parameters<typeof PackCreateModal>[0]> = {}) {
  const onClose = vi.fn();
  const onCreated = vi.fn();
  render(
    <PackCreateModal
      open
      preset={PRESET}
      onClose={onClose}
      onCreated={onCreated}
      {...overrides}
    />,
  );
  return { onClose, onCreated };
}

beforeEach(() => {
  createMock.mockReset();
  toastMock.mockReset();
  pickerProps.current = {};
  createMock.mockResolvedValue({ id: 'p1', name: '6036 黑', memberCount: 2 });
});

describe('PackCreateModal · 成员', () => {
  it('从批量条进来时把选中的素材直接铺开，不用再选一次', () => {
    renderModal();
    expect(screen.getByText('已选 2 张')).toBeInTheDocument();
    expect(screen.getByText('6036-黑-正面')).toBeInTheDocument();
    expect(screen.getByText('6036-黑-立体')).toBeInTheDocument();
  });

  it('没有成员时「创建封装」不可点，并给一个能直接点的空态', () => {
    renderModal({ preset: [] });
    expect(screen.getByRole('button', { name: '创建封装' })).toBeDisabled();
    // 空态是一扇门，不是一句「还没有素材」
    expect(screen.getByRole('button', { name: /点这里选几张/ })).toBeInTheDocument();
  });

  it('可以就地把某张移出这一批', async () => {
    const user = userEvent.setup();
    renderModal();
    await user.click(screen.getByRole('button', { name: '移除 6036-黑-立体' }));
    expect(screen.getByText('已选 1 张')).toBeInTheDocument();
  });

  it('从选择器确认后整批替换，顺序按用户选的顺序', async () => {
    const user = userEvent.setup();
    renderModal({ preset: [] });
    await user.click(screen.getByRole('button', { name: /点这里选几张/ }));
    await user.click(screen.getByRole('button', { name: '模拟确认选择' }));
    expect(screen.getByText('已选 2 张')).toBeInTheDocument();
    // 交回来的顺序原样保留（包内顺序 = 用户选的顺序）
    const names = screen.getAllByTitle(/6036-黑/).map((node) => node.getAttribute('title'));
    expect(names[0]).toBe('6036-黑-细节.png');
  });
});

describe('PackCreateModal · 提交', () => {
  it('名字留空也允许提交，交给后端记成「未命名封装」', async () => {
    const user = userEvent.setup();
    const { onClose, onCreated } = renderModal();
    await user.click(screen.getByRole('button', { name: '创建封装' }));

    expect(createMock).toHaveBeenCalledWith({
      name: '',
      assetIds: ['m1', 'm2'],
    });
    expect(toastMock).toHaveBeenCalled();
    expect(onCreated).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it('填了名字就按填的提交', async () => {
    const user = userEvent.setup();
    renderModal();
    await user.type(screen.getByLabelText('封装名称'), '6036 黑');
    await user.click(screen.getByRole('button', { name: '创建封装' }));
    expect(createMock).toHaveBeenCalledWith({ name: '6036 黑', assetIds: ['m1', 'm2'] });
  });

  it('失败时错误就地显示、不关弹窗（用户要对着素材核对是哪张）', async () => {
    const user = userEvent.setup();
    createMock.mockRejectedValue(new Error('这些素材已经属于封装「6036 白」'));
    const { onClose, onCreated } = renderModal();

    await user.click(screen.getByRole('button', { name: '创建封装' }));

    expect(screen.getByText('这些素材已经属于封装「6036 白」')).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    expect(onCreated).not.toHaveBeenCalled();
    // 素材还在，改完可以再点一次
    expect(screen.getByText('6036-黑-正面')).toBeInTheDocument();
  });
});

describe('PackCreateModal · 选择器里已进包的素材', () => {
  it('传下去的原因函数只对「不属于这一批」的素材给原因', async () => {
    const user = userEvent.setup();
    renderModal();
    await user.click(screen.getByRole('button', { name: '选择素材' }));

    const reason = pickerProps.current.unavailableReason as (
      asset: LibraryAssetOut,
    ) => string | null;
    // 已经在别的封装里 → 说清是哪个包（用户才知道该去改哪个）
    expect(reason(assetOf('x', '别的.png', { packId: 'p9', packName: '6036 白' }))).toBe(
      '已在封装「6036 白」里',
    );
    // 没进过包 → 可选
    expect(reason(assetOf('y', '散图.png'))).toBeNull();
    // 已经是这一批的成员 → 不能说「已在封装里」（那会让人以为选不了自己选的）
    expect(reason(assetOf('m1', '6036-黑-正面.png', { packId: 'p9', packName: '旧包' }))).toBeNull();
  });
});
