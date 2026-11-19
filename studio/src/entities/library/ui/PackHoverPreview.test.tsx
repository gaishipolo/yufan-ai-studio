import {act, render, screen} from '@testing-library/react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {PackHoverPreview} from './PackHoverPreview';

/**
 * 包预览浮层：素材的悬停是「放大这一张」，包的是「摊开里面有哪些」。
 *
 * 包没有自己的字节（封面只是第一个成员的），放大那张封面等于把这个包
 * 当成一张普通图 —— 用户看不到它还有三视图和细节图。
 */

const {packMock} = vi.hoisted(() => ({packMock: vi.fn()}));

vi.mock('../api/library.queries', () => ({
  useLibraryPack: (id: string | null) => {
    packMock(id);
    return id ? {data: packData.current} : {data: undefined};
  },
}));

const packData = {current: null as unknown};

function packOf(members: {id: string; name: string; deleted?: boolean}[]) {
  return {
    id: 'p1',
    name: '6036 黑',
    coverUrl: null,
    memberCount: members.length,
    tagIds: [],
    members: members.map(member => ({
      kind: 'image',
      coverUrl: null,
      tagIds: [],
      deleted: false,
      ...member,
    })),
  };
}

const POINTER = {x: 10, y: 10};

beforeEach(() => {
  vi.useFakeTimers();
  packMock.mockReset();
  packData.current = packOf([
    {id: 'm1', name: '6036-黑-正面.png'},
    {id: 'm2', name: '6036-黑-三视图.png'},
  ]);
});

afterEach(() => {
  vi.useRealTimers();
});

describe('PackHoverPreview', () => {
  it('停住之后摊开成员，而不是只放大封面', () => {
    render(<PackHoverPreview packId="p1" pointer={POINTER} />);
    act(() => {
      vi.advanceTimersByTime(300);
    });

    expect(screen.getByText('6036 黑')).toBeInTheDocument();
    expect(screen.getByText('2 张')).toBeInTheDocument();
    // 每张成员各占一格（用 title 定位，缩略图本身没有文字）
    expect(screen.getByTitle('6036-黑-正面.png')).toBeInTheDocument();
    expect(screen.getByTitle('6036-黑-三视图.png')).toBeInTheDocument();
  });

  it('快速划过不出浮层，也不发请求（列表里几十个包，划过一排不该打一串请求）', () => {
    render(<PackHoverPreview packId="p1" pointer={POINTER} />);
    act(() => {
      vi.advanceTimersByTime(50);
    });
    expect(screen.queryByText('6036 黑')).not.toBeInTheDocument();
    // 延迟没到就不该把 id 交给查询
    expect(packMock).not.toHaveBeenCalledWith('p1');
  });

  it('没有悬停任何包时不渲染', () => {
    render(<PackHoverPreview packId={null} pointer={POINTER} />);
    act(() => {
      vi.advanceTimersByTime(300);
    });
    expect(packMock).toHaveBeenCalledWith(null);
  });

  it('成员超过 6 张时只摊 6 张并报出还有几张（浮层不能盖满屏幕）', () => {
    packData.current = packOf(
      Array.from({length: 9}, (_, index) => ({id: `m${index}`, name: `图-${index}.png`})),
    );
    render(<PackHoverPreview packId="p1" pointer={POINTER} />);
    act(() => {
      vi.advanceTimersByTime(300);
    });

    expect(screen.getByText('9 张')).toBeInTheDocument();
    expect(screen.getByText('还有 3 张')).toBeInTheDocument();
    expect(screen.getByTitle('图-0.png')).toBeInTheDocument();
    expect(screen.queryByTitle('图-8.png')).not.toBeInTheDocument();
  });

  it('回收站里的成员照列表并标「已删除」（少一张却不说明更糟）', () => {
    packData.current = packOf([
      {id: 'm1', name: '6036-黑-正面.png'},
      {id: 'm2', name: '6036-黑-三视图.png', deleted: true},
    ]);
    render(<PackHoverPreview packId="p1" pointer={POINTER} />);
    act(() => {
      vi.advanceTimersByTime(300);
    });

    expect(screen.getByTitle('6036-黑-三视图.png')).toBeInTheDocument();
    expect(screen.getByText('已删除')).toBeInTheDocument();
  });
});
