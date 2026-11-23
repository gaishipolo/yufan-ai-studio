import {render, screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {describe, expect, it, vi} from 'vitest';
import {ConfirmDialog} from './ConfirmDialog';

describe('ConfirmDialog', () => {
  it('没打开时不渲染任何东西', () => {
    render(
      <ConfirmDialog open={false} title="确认？" onCancel={() => {}} onConfirm={() => {}} />,
    );
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('摊开事实清单：每行一个标签与一个值', () => {
    render(
      <ConfirmDialog
        open
        title="确认生成？"
        details={[
          {label: '模板', value: '商品替换 v3'},
          {label: '模型', value: 'LibTV'},
        ]}
        onCancel={() => {}}
        onConfirm={() => {}}
      />,
    );
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveTextContent('模板');
    expect(dialog).toHaveTextContent('商品替换 v3');
    expect(dialog).toHaveTextContent('模型');
    expect(dialog).toHaveTextContent('LibTV');
  });

  it('取消与确认各走各的回调', async () => {
    const user = userEvent.setup();
    const onCancel = vi.fn();
    const onConfirm = vi.fn();
    render(
      <ConfirmDialog
        open
        title="确认生成？"
        onCancel={onCancel}
        onConfirm={onConfirm}
      />,
    );

    await user.click(screen.getByRole('button', {name: '取消'}));
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', {name: '确认'}));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it('提醒条有内容时才渲染', () => {
    const {rerender} = render(
      <ConfirmDialog open title="确认？" onCancel={() => {}} onConfirm={() => {}} />,
    );
    expect(screen.queryByText(/消耗账号积分/)).not.toBeInTheDocument();

    rerender(
      <ConfirmDialog
        open
        title="确认？"
        notice="会消耗账号积分。"
        onCancel={() => {}}
        onConfirm={() => {}}
      />,
    );
    expect(screen.getByText(/消耗账号积分/)).toBeInTheDocument();
  });

  it('破坏性动作：确认按钮是红色的', () => {
    render(
      <ConfirmDialog
        open
        title="移进回收站？"
        confirmTone="danger"
        confirmLabel="移进回收站"
        onCancel={() => {}}
        onConfirm={() => {}}
      />,
    );
    expect(screen.getByRole('button', {name: '移进回收站'})).toHaveClass('bg-danger');
  });

  it('提交中：确认按钮禁用、取消也禁用', () => {
    render(
      <ConfirmDialog
        open
        title="确认？"
        busy
        onCancel={() => {}}
        onConfirm={() => {}}
      />,
    );
    expect(screen.getByRole('button', {name: '确认'})).toBeDisabled();
    expect(screen.getByRole('button', {name: '取消'})).toBeDisabled();
  });
});
