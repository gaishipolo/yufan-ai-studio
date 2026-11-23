import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';
import { useToastStore } from '@/shared/lib/toast';
import { Toaster } from './Toaster';

function push(tone: 'info' | 'success' | 'warning' | 'error', message: string) {
  useToastStore.getState().push(message, tone);
}

describe('Toaster', () => {
  beforeEach(() => {
    useToastStore.setState({items: []});
  });

  it('四种色调分得清：信息、成功、警告、错误各归各的', () => {
    push('info', '随口说一句');
    push('success', '已保存');
    push('warning', '注意：这条要留久一点');
    push('error', '失败了');
    const {container} = render(<Toaster />);

    const tones = Array.from(container.querySelectorAll('[data-tone]')).map(node =>
      node.getAttribute('data-tone'),
    );
    expect(tones).toEqual(['info', 'success', 'warning', 'error']);
  });

  it('错误会打断朗读，其余不打断', () => {
    push('error', '失败了');
    push('success', '已保存');
    render(<Toaster />);

    expect(screen.getByRole('alert')).toHaveTextContent('失败了');
    expect(screen.getByRole('status')).toHaveTextContent('已保存');
  });

  it('不靠颜色单独承载语义：读屏先听到类型', () => {
    push('error', '失败了');
    render(<Toaster />);

    expect(screen.getByText('错误：')).toBeInTheDocument();
  });

  it('浮层不拦底下的操作：容器本身不吃点击', () => {
    push('success', '已保存');
    const {container} = render(<Toaster />);

    expect(container.firstChild).toHaveClass('pointer-events-none');
  });

  it('最多显示 4 条：再多就把页面盖住了', () => {
    for (let i = 0; i < 6; i += 1) push('info', `第 ${i + 1} 条`);
    render(<Toaster />);

    expect(screen.getAllByRole('status')).toHaveLength(4);
    expect(screen.queryByText('第 1 条')).not.toBeInTheDocument();
    expect(screen.getByText('第 6 条')).toBeInTheDocument();
  });

  it('可以手动关掉：关闭按钮只关自己那一条', async () => {
    const user = userEvent.setup();
    push('error', '失败了');
    push('info', '随口说一句');
    render(<Toaster />);

    await user.click(screen.getAllByRole('button', {name: '关闭提示'})[0]);

    expect(screen.queryByText('失败了')).not.toBeInTheDocument();
    expect(screen.getByText('随口说一句')).toBeInTheDocument();
  });
});
