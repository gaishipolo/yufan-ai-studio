import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ShareRequestOut } from '@/shared/api/schema';
import { ShareRequestsModal } from './ShareRequestsModal';

const { decideMock, cancelMock, state } = vi.hoisted(() => ({
  decideMock: vi.fn(),
  cancelMock: vi.fn(),
  state: { items: [] as ShareRequestOut[] },
}));

vi.mock('@/entities/library/api/library.queries', () => ({
  useShareRequests: (scope: string) => ({
    data: scope === 'pending' ? state.items : [],
    isPending: false,
    error: null,
  }),
  useDecideShareRequest: () => ({ mutate: decideMock }),
  useCancelShareRequest: () => ({ mutate: cancelMock }),
}));

/** 一条待审的共享申请：快照版本与当前版本一致（资产没被改过） */
const REQUEST: ShareRequestOut = {
  id: 'r1',
  assetId: 'a1',
  assetName: '产品图.png',
  stateVersion: 7,
  requestStateVersion: 7,
  applicantId: 'u2',
  applicantName: '小李',
  action: 'share',
  target: 'group',
  groupId: 'g1',
  groupName: '一组',
  status: 'pending',
  createdAt: '2026-11-23T02:00:00Z',
};

beforeEach(() => {
  decideMock.mockReset();
  cancelMock.mockReset();
  state.items = [REQUEST];
});

function renderModal() {
  return render(<ShareRequestsModal open onClose={vi.fn()} />);
}

describe('ShareRequestsModal · 审批要带上版本', () => {
  it('通过时回传审批人看到的资产版本，而不是 0', async () => {
    const user = userEvent.setup();
    renderModal();

    await user.click(screen.getByRole('button', { name: '通过' }));

    expect(decideMock).toHaveBeenCalledWith(
      expect.objectContaining({
        id: 'r1',
        approve: true,
        body: { expectedVersion: 7 },
      }),
      expect.anything(),
    );
  });

  it('驳回时不带版本：驳回过时的申请单是正当操作', async () => {
    const user = userEvent.setup();
    renderModal();

    await user.click(screen.getByRole('button', { name: '驳回' }));

    const call = decideMock.mock.calls[0][0] as {
      approve: boolean;
      body: { expectedVersion: number };
    };
    expect(call.approve).toBe(false);
    expect(call.body.expectedVersion).toBe(0);
  });
});

describe('ShareRequestsModal · 资产被改过时给审批人提示', () => {
  it('快照版本与当前版本对不上就标出「资产已变更」', () => {
    state.items = [{ ...REQUEST, stateVersion: 9, requestStateVersion: 7 }];
    renderModal();

    expect(screen.getByText('资产已变更')).toBeInTheDocument();
  });

  it('版本一致时不标', () => {
    renderModal();

    expect(screen.queryByText('资产已变更')).not.toBeInTheDocument();
  });

  it('已处理的单子不再提示', () => {
    state.items = [{ ...REQUEST, status: 'approved', stateVersion: 9, requestStateVersion: 7 }];
    renderModal();

    expect(screen.queryByText('资产已变更')).not.toBeInTheDocument();
  });
});
