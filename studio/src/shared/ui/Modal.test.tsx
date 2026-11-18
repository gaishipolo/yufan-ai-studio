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

  /* 叠层：建号弹窗里开「新建角色」，两层叠着。
     以前每个弹窗各自监听 window 的 Escape，两层一起关 ——
     用户填了一半的登录名就没了。栈顶隔离是这层交互的前提。 */
  describe('叠层', () => {
    function Stacked() {
      const [outer, setOuter] = useState(false);
      const [inner, setInner] = useState(false);
      return (
        <>
          <button type="button" onClick={() => setOuter(true)}>
            打开建号
          </button>
          <Modal open={outer} onClose={() => setOuter(false)} title="新建用户">
            <button type="button" onClick={() => setInner(true)}>
              打开新建角色
            </button>
          </Modal>
          <Modal open={inner} onClose={() => setInner(false)} title="新建角色">
            <p>角色表单</p>
          </Modal>
        </>
      );
    }

    it('按一次 Esc 只关栈顶那层，下层还在', async () => {
      const user = userEvent.setup();
      render(<Stacked />);

      await user.click(screen.getByRole('button', { name: '打开建号' }));
      await user.click(screen.getByRole('button', { name: '打开新建角色' }));
      expect(screen.getByRole('dialog', { name: '新建角色' })).toBeInTheDocument();

      await user.keyboard('{Escape}');

      expect(screen.queryByRole('dialog', { name: '新建角色' })).not.toBeInTheDocument();
      expect(screen.getByRole('dialog', { name: '新建用户' })).toBeInTheDocument();
    });

    it('栈顶那层的 z-index 比下层高（后开的在上面）', async () => {
      const user = userEvent.setup();
      render(<Stacked />);

      await user.click(screen.getByRole('button', { name: '打开建号' }));
      await user.click(screen.getByRole('button', { name: '打开新建角色' }));

      const zOf = (name: string) => {
        const dialog = screen.getByRole('dialog', { name });
        const overlay = dialog.parentElement;
        return Number(overlay?.style.zIndex ?? '0');
      };
      expect(zOf('新建角色')).toBeGreaterThan(zOf('新建用户'));
    });

    it('两层都关掉后滚动恢复', async () => {
      const user = userEvent.setup();
      render(<Stacked />);

      await user.click(screen.getByRole('button', { name: '打开建号' }));
      await user.click(screen.getByRole('button', { name: '打开新建角色' }));
      await user.keyboard('{Escape}');
      await user.keyboard('{Escape}');

      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      expect(document.body.style.overflow).toBe('');
    });
  });
});
