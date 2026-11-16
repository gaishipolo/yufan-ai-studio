import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AssetPreview } from './AssetPreview';

const { detailMock, mediaMock } = vi.hoisted(() => ({
  detailMock: vi.fn(),
  mediaMock: vi.fn(),
}));

vi.mock('@/entities/library/api/library.queries', () => ({
  useLibraryDetail: detailMock,
  useLibraryMedia: mediaMock,
}));

beforeEach(() => {
  detailMock.mockReturnValue({
    data: { id: 'a1', kind: 'video', coverUrl: 'https://cdn/cover.jpg', width: 16, height: 9 },
  });
  mediaMock.mockReturnValue({ url: undefined, isPending: false, isError: true });
});

describe('AssetPreview · 取流失败不是「正在读取」', () => {
  it('取流失败时显示「视频暂时读不出来」，不再永远停在「正在读取视频…」', async () => {
    const user = userEvent.setup();
    render(<AssetPreview assetId="a1" />);

    await user.click(screen.getByRole('button', { name: '播放案例视频' }));

    expect(screen.queryByText('正在读取视频…')).not.toBeInTheDocument();
    expect(screen.getByText('视频暂时读不出来')).toBeInTheDocument();
  });

  it('取流进行中仍然是「正在读取视频…」', async () => {
    mediaMock.mockReturnValue({ url: undefined, isPending: true, isError: false });
    const user = userEvent.setup();
    render(<AssetPreview assetId="a1" />);

    await user.click(screen.getByRole('button', { name: '播放案例视频' }));

    expect(screen.getByText('正在读取视频…')).toBeInTheDocument();
  });
});
