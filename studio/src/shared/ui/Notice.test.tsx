import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Notice } from './Notice';

describe('Notice', () => {
  it('错误用 alert，其余用 status：不该打断读屏的时候别打断', () => {
    const { unmount } = render(<Notice tone="error">提交失败</Notice>);
    expect(screen.getByRole('alert')).toHaveTextContent('提交失败');
    unmount();

    render(<Notice tone="success">已保存</Notice>);
    expect(screen.getByRole('status')).toHaveTextContent('已保存');
  });

  it('四种色调各有各的配色，不会被默认色吃掉', () => {
    const seen = new Set<string>();
    for (const tone of ['info', 'success', 'warning', 'error'] as const) {
      const { container, unmount } = render(<Notice tone={tone}>文案</Notice>);
      seen.add(container.firstElementChild?.className ?? '');
      unmount();
    }
    // 四份 className 互不相同 = 没有两档共用一套颜色
    expect(seen.size).toBe(4);
  });

  it('title 与正文分开渲染，正文保留换行', () => {
    render(
      <Notice tone="warning" title="有 2 项需要处理">
        {'第一行\n第二行'}
      </Notice>,
    );
    expect(screen.getByText('有 2 项需要处理')).toBeInTheDocument();
    expect(screen.getByText(/第一行/)).toHaveClass('whitespace-pre-wrap');
  });

  it('不给 onClose 就没有关闭按钮', () => {
    render(<Notice>只读提示</Notice>);
    expect(screen.queryByRole('button', { name: '关闭提示' })).not.toBeInTheDocument();
  });

  it('给了 onClose 才出现关闭按钮，点击回调一次', async () => {
    const onClose = vi.fn();
    render(<Notice onClose={onClose}>可关闭</Notice>);

    await userEvent.click(screen.getByRole('button', { name: '关闭提示' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
