import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { TextVersionHistory } from './TextVersionHistory';

const { versionsMock, currentMock, restoreMock, versionMock, toastMock } = vi.hoisted(() => ({
  versionsMock: vi.fn(),
  currentMock: vi.fn(),
  restoreMock: vi.fn(),
  versionMock: vi.fn(),
  toastMock: vi.fn(),
}));

vi.mock('@/entities/library/api/library.queries', () => ({
  useTextVersions: versionsMock,
  useReadText: currentMock,
  useRestoreTextVersion: () => ({ mutate: restoreMock, isPending: false }),
  useTextVersion: versionMock,
}));
vi.mock('@/shared/lib/toast', () => ({ toast: toastMock }));

const VERSIONS = [
  {
    version: 1,
    createdAt: '2026-11-01T02:00:00Z',
    createdBy: 'u1',
    sha256: 'abcdef1234567890',
    excerpt: '第一版正文',
    note: '',
  },
];

beforeEach(() => {
  versionsMock.mockReset();
  currentMock.mockReset();
  restoreMock.mockReset();
  versionMock.mockReset();
  toastMock.mockReset();
  versionsMock.mockReturnValue({ data: VERSIONS, isPending: false, isError: false });
  versionMock.mockReturnValue({ data: undefined, isPending: false, isError: false });
  currentMock.mockReturnValue({
    data: { id: 't1', content: '当前正文', version: 7 },
    isPending: false,
    isError: false,
  });
});

describe('TextVersionHistory · 恢复要带乐观锁', () => {
  it('当前版本还没读到时「恢复到此版」不可点：不带版本号的恢复会覆盖别人的改动', () => {
    currentMock.mockReturnValue({ data: undefined, isPending: true, isError: false });
    render(<TextVersionHistory open assetId="t1" assetName="脚本" onClose={vi.fn()} />);

    expect(screen.getByRole('button', { name: '恢复到此版' })).toBeDisabled();
    expect(restoreMock).not.toHaveBeenCalled();
  });

  it('当前版本读到了才放行，且请求带上 expectedVersion', async () => {
    const user = userEvent.setup();
    render(<TextVersionHistory open assetId="t1" assetName="脚本" onClose={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: '恢复到此版' }));

    expect(restoreMock).toHaveBeenCalledWith(
      { id: 't1', body: { version: 1, expectedVersion: 7 } },
      expect.anything(),
    );
  });
});
