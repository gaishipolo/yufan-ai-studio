import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AssetPreview } from './AssetPreview';

const { detailMock, mediaMock, largeMock } = vi.hoisted(() => ({
  detailMock: vi.fn(),
  mediaMock: vi.fn(),
  largeMock: vi.fn(),
}));

vi.mock('@/entities/library/api/library.queries', () => ({
  useLibraryDetail: detailMock,
  useLibraryMedia: mediaMock,
  useLibraryLargePreview: largeMock,
}));

beforeEach(() => {
  detailMock.mockReturnValue({
    data: { id: 'a1', kind: 'video', coverUrl: 'https://cdn/cover.jpg', width: 16, height: 9 },
  });
  mediaMock.mockReturnValue({ url: undefined, isPending: false, isError: true });
  largeMock.mockReturnValue({ url: undefined });
});

describe('AssetPreview · 封面走同源，不直连对象存储', () => {
  it('同源大图就绪时用它当封面，不用 S3 直链', () => {
    /* 局域网其他主机连不上 S3 内网直链（9000），只有后端那台能解析。
       封面必须由后端带鉴权取字节再转 object URL，任何主机都能显示。 */
    largeMock.mockReturnValue({ url: 'blob:same-origin-cover' });
    render(<AssetPreview assetId="a1" />);
    const img = screen.getByAltText('展示案例') as HTMLImageElement;
    expect(img.src).toContain('blob:');
    expect(img.src).not.toContain('cdn/cover.jpg');
  });

  it('同源大图没就绪时回退详情的 coverUrl，不留空封面', () => {
    render(<AssetPreview assetId="a1" />);
    expect((screen.getByAltText('展示案例') as HTMLImageElement).src).toContain(
      'cdn/cover.jpg',
    );
  });
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
