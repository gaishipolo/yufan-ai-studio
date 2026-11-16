import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { LibraryAssetOut } from '@/shared/api/schema';
import { LibraryShareModal } from './LibraryShareModal';

const { requestMock, toastMock } = vi.hoisted(() => ({
  requestMock: vi.fn(),
  toastMock: vi.fn(),
}));

vi.mock('@/entities/library/api/library.queries', () => ({
  useCreateShareRequest: () => ({ mutate: requestMock, isPending: false }),
}));
vi.mock('@/shared/lib/toast', () => ({ toast: toastMock }));

const ASSET: LibraryAssetOut = {
  id: 'a1',
  name: '产品图.png',
  kind: 'image',
  origin: 'upload',
  ownerId: 'u1',
  ownerName: '老大',
  visibility: 'group',
  groupIds: ['g1'],
  state: 'active',
  sizeBytes: 1024,
  createdAt: '2026-11-10T02:00:00Z',
  tags: [],
};

beforeEach(() => {
  requestMock.mockReset();
  toastMock.mockReset();
});

/** 把后端回的申请单状态喂给组件，模拟一次提交完成 */
function settle(status: string) {
  const options = requestMock.mock.calls[0][1] as { onSuccess: (result: { status: string }) => void };
  act(() => options.onSuccess({ status }));
}

describe('LibraryShareModal · 撤销共享的提示按实际结果分', () => {
  it('后端当场通过（审核人就是自己）时说「已撤销共享」', async () => {
    const user = userEvent.setup();
    render(
      <LibraryShareModal
        asset={ASSET}
        mode="revoke"
        groups={[{ id: 'g1', name: '一组' }]}
        canDirect={false}
        isOwner
        onClose={vi.fn()}
      />,
    );

    await user.type(
      screen.getByPlaceholderText('例：这版人物形象已废弃，换成 v3 重新上传'),
      '这版不再用了',
    );
    await user.click(screen.getByRole('button', { name: '提交撤销申请' }));

    settle('approved');

    expect(toastMock).toHaveBeenCalledWith('已撤销共享', 'success');
  });

  it('还在等审批时说明要审批通过才生效', async () => {
    const user = userEvent.setup();
    render(
      <LibraryShareModal
        asset={ASSET}
        mode="revoke"
        groups={[{ id: 'g1', name: '一组' }]}
        canDirect={false}
        isOwner
        onClose={vi.fn()}
      />,
    );

    await user.type(
      screen.getByPlaceholderText('例：这版人物形象已废弃，换成 v3 重新上传'),
      '这版不再用了',
    );
    await user.click(screen.getByRole('button', { name: '提交撤销申请' }));

    settle('pending');

    expect(toastMock).toHaveBeenCalledWith('撤销申请已提交，审批通过后才会生效', 'success');
  });
});
