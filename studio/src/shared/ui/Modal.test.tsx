import { useState } from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Modal } from './Modal';

/** 复现真实用法：弹窗由外部按钮打开，用于验证焦点归还 */
function Harness() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        打开详情
      </button>
      <Modal open={open} onClose={() => setOpen(false)} title="任务详情">
        <p>弹窗内容</p>
      </Modal>
    </>
  );
}

describe('Modal', () => {
  it('用标题作为可访问名称（aria-labelledby）', async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(screen.getByRole('button', { name: '打开详情' }));

    expect(screen.getByRole('dialog', { name: '任务详情' })).toBeInTheDocument();
  });

  it('打开时锁滚动，关闭后恢复原值', async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(screen.getByRole('button', { name: '打开详情' }));
    expect(document.body.style.overflow).toBe('hidden');

    await user.click(screen.getByRole('button', { name: '关闭' }));
    expect(document.body.style.overflow).toBe('');
  });

  it('打开后焦点进入弹窗', async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(screen.getByRole('button', { name: '打开详情' }));

    await waitFor(() => {
      expect(document.activeElement).toBe(screen.getByRole('dialog', { name: '任务详情' }));
    });
  });

  it('Esc 关闭，并把焦点归还触发元素', async () => {
    const user = userEvent.setup();
    render(<Harness />);

    const trigger = screen.getByRole('button', { name: '打开详情' });
    await user.click(trigger);
    await waitFor(() => expect(screen.getByRole('dialog')).toHaveFocus());

    await user.keyboard('{Escape}');

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it('未打开时不渲染任何内容', () => {
    render(
      <Modal open={false} onClose={vi.fn()} title="任务详情">
        <p>弹窗内容</p>
      </Modal>,
    );
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});
