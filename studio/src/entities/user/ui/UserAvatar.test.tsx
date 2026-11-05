import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { UserAvatar } from './UserAvatar';

describe('UserAvatar', () => {
  it('没有头像地址时显示昵称首字', () => {
    render(<UserAvatar user={{ displayName: '张伟', username: 'zhangwei' }} />);

    expect(screen.getByText('张')).toBeInTheDocument();
  });

  it('昵称为空时退到登录名，而不是显示空白', () => {
    render(<UserAvatar user={{ displayName: '  ', username: 'alice' }} />);

    expect(screen.getByText('A')).toBeInTheDocument();
  });

  it('有地址就渲染图片，且不带 alt（装饰性，品牌名在别处）', () => {
    render(<UserAvatar user={{ displayName: '张三', avatarUrl: 'https://x/a.png' }} />);

    const img = document.querySelector('img');
    expect(img).toHaveAttribute('src', 'https://x/a.png');
    expect(img).toHaveAttribute('alt', '');
  });

  it('图片加载失败时无声退回首字，不出现碎图', () => {
    render(<UserAvatar user={{ displayName: '李四', avatarUrl: 'https://x/broken.png' }} />);

    fireEvent.error(document.querySelector('img') as HTMLImageElement);

    expect(document.querySelector('img')).toBeNull();
    expect(screen.getByText('李')).toBeInTheDocument();
  });

  it('换了新地址会重新尝试加载（失败状态按 URL 记忆，不是一次性布尔量）', () => {
    const { rerender } = render(
      <UserAvatar user={{ displayName: '王五', avatarUrl: 'https://x/broken.png' }} />,
    );
    fireEvent.error(document.querySelector('img') as HTMLImageElement);

    rerender(<UserAvatar user={{ displayName: '王五', avatarUrl: 'https://x/ok.png' }} />);

    expect(document.querySelector('img')).toHaveAttribute('src', 'https://x/ok.png');
  });

  it('按 shape 决定圆角，尺寸走内联样式（避免各调用方各写一套 class）', () => {
    const { rerender } = render(<UserAvatar user={{ displayName: '赵' }} size={40} />);
    const circle = screen.getByText('赵');
    expect(circle.className).toContain('rounded-full');
    expect(circle).toHaveStyle({ width: '40px', height: '40px' });

    rerender(<UserAvatar user={{ displayName: '赵' }} size={30} shape="square" tone="accent" />);
    const square = screen.getByText('赵');
    expect(square.className).toContain('rounded-[9px]');
    expect(square.className).toContain('text-white');
  });
});
