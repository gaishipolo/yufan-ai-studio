import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { LibraryAssetOut } from '@/shared/api/schema';
import { PackCreateModal } from './PackCreateModal';

const { createMock, updateMock, toastMock, pickerProps } = vi.hoisted(() => ({
  createMock: vi.fn(),
  updateMock: vi.fn(),
  toastMock: vi.fn(),
  pickerProps: { current: {} as Record<string, unknown> },
}));

vi.mock('@/entities/library/api/library.queries', () => ({
  useCreatePack: () => ({ mutateAsync: createMock, isPending: false }),
  useUpdatePack: () => ({ mutateAsync: updateMock, isPending: false }),
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

const PRODUCT_TAG = {id: 'tag-6036', name: '6036'};

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
    /* 共同标签是建封装的前提：没有它的用例在别处单独测 */
    tags: [PRODUCT_TAG],
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
  updateMock.mockReset();
  toastMock.mockReset();
  pickerProps.current = {};
  createMock.mockResolvedValue({ id: 'p1', name: '6036 黑', memberCount: 2 });
  updateMock.mockResolvedValue({ id: 'p1', name: '6036 黑', memberCount: 2 });
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

describe('PackCreateModal · 共同标签', () => {
  /* 封装的意思是「同一件东西的几个视角」，而这件事在系统里唯一能判的信号就是
     标签。没有共同标签的一组图，要么不是一回事，要么还没打标 —— 后端 400，
     但让人填完名字、点了按钮才被拒，比一开始就说清差得多。 */
  it('成员没有共同标签时：按钮不可点，并说清该怎么办', () => {
    renderModal({
      preset: [
        assetOf('m1', '正面.png', { tags: [{ id: 'tag-front', name: '正面图' }] }),
        assetOf('m2', '三视图.png', { tags: [{ id: 'tag-views', name: '三视图' }] }),
      ],
    });

    expect(screen.getByRole('button', { name: '创建封装' })).toBeDisabled();
    expect(screen.getByText(/没有共同的标签/)).toBeInTheDocument();
    // 死按钮必须给下一步，而不是只说「不行」
    expect(screen.getByText(/打上同一个标签（比如产品编号）/)).toBeInTheDocument();
  });

  it('只共有一个标签就够了（判据是「有没有」，不是「有几个」）', async () => {
    const user = userEvent.setup();
    renderModal({
      preset: [
        assetOf('m1', '正面.png', {
          tags: [PRODUCT_TAG, { id: 'tag-front', name: '正面图' }],
        }),
        assetOf('m2', '散图.png', { tags: [PRODUCT_TAG] }),
      ],
    });

    expect(screen.queryByText(/没有共同的标签/)).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '创建封装' }));
    expect(createMock).toHaveBeenCalledWith({ name: '', assetIds: ['m1', 'm2'] });
  });

  it('共同标签只有两个成员都有时才算（不是看第一个成员的）', () => {
    renderModal({
      preset: [
        assetOf('m1', 'a.png', {
          tags: [PRODUCT_TAG, { id: 'tag-front', name: '正面图' }],
        }),
        assetOf('m2', 'b.png', { tags: [{ id: 'tag-other', name: '别的' }] }),
      ],
    });
    // m1 有 6036，m2 没有 → 不是共同标签
    expect(screen.getByRole('button', { name: '创建封装' })).toBeDisabled();
  });
});

describe('PackCreateModal · 编辑已有封装', () => {
  const EDITING = {
    id: 'p1',
    name: '6036 黑',
    members: [
      {id: 'm1', name: '6036-黑-正面.png', kind: 'image', coverUrl: null, tagIds: ['tag-6036'], deleted: false},
      {id: 'm2', name: '6036-黑-立体.png', kind: 'image', coverUrl: null, tagIds: ['tag-6036'], deleted: false},
    ],
  };

  it('名字与成员预填，按钮是「保存」而不是「创建封装」', () => {
    renderModal({ pack: EDITING, preset: [] });
    expect(screen.getByLabelText('封装名称')).toHaveValue('6036 黑');
    expect(screen.getByText('已选 2 张')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '保存' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '创建封装' })).not.toBeInTheDocument();
  });

  it('提交走 PATCH（改），不是新建', async () => {
    const user = userEvent.setup();
    const { onClose, onCreated } = renderModal({ pack: EDITING, preset: [] });

    await user.type(screen.getByLabelText('封装名称'), '（改）');
    await user.click(screen.getByRole('button', { name: '保存' }));

    expect(updateMock).toHaveBeenCalledWith({
      id: 'p1',
      name: '6036 黑（改）',
      assetIds: ['m1', 'm2'],
    });
    expect(createMock).not.toHaveBeenCalled();
    expect(onCreated).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });

  it('移掉一张再保存，提交的是新的成员列表', async () => {
    const user = userEvent.setup();
    renderModal({ pack: EDITING, preset: [] });

    await user.click(screen.getByRole('button', { name: '移除 6036-黑-立体' }));
    await user.click(screen.getByRole('button', { name: '保存' }));

    expect(updateMock).toHaveBeenCalledWith({
      id: 'p1',
      name: '6036 黑',
      assetIds: ['m1'],
    });
  });

  it('属于这个包自己的素材不该被标成「已在封装里」', () => {
    renderModal({ pack: EDITING, preset: [] });
    const reason = pickerProps.current.unavailableReason as (
      asset: LibraryAssetOut,
    ) => string | null;

    // 本包的成员：能再选回来
    expect(reason(assetOf('x', '本包的.png', { packId: 'p1', packName: '6036 黑' }))).toBeNull();
    // 别的包：拦
    expect(reason(assetOf('y', '别家的.png', { packId: 'p2', packName: '6036 白' }))).toBe(
      '已在封装「6036 白」里',
    );
  });
});
