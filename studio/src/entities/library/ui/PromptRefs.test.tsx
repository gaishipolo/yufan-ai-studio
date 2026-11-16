import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { PromptRefs } from './PromptRefs';

/** 详情缺失是这里要验的场景：取不到封面与原图 */
vi.mock('@/entities/library/api/library.queries', () => ({
  useLibraryDetail: () => ({ data: undefined }),
  useLibraryMedia: () => ({ url: undefined, isPending: false, isError: false }),
  useReadText: () => ({ data: undefined, isPending: false, isError: false }),
}));

describe('PromptRefs · 大图弹层', () => {
  it('详情缺失时给「素材不可用」占位，不渲染没有 src 的破图', async () => {
    const user = userEvent.setup();
    render(<PromptRefs value="看这张 {{Mixed 1}}" referenceIds={['a1']} />);

    await user.click(screen.getByRole('button', { name: '点击查看大图' }));

    expect(screen.getByText('素材不可用')).toBeInTheDocument();
    expect(document.querySelector('img[src=""]')).toBeNull();
    expect(document.querySelector('img')).toBeNull();
  });
});
