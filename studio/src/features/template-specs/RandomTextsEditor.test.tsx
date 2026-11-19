import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { RandomTextsEditor } from './RandomTextsEditor';

describe('随机文案编辑器', () => {
  it('至少保留一条：只有一条时删除按钮禁用', () => {
    render(<RandomTextsEditor texts={['白天']} onChange={vi.fn()} />);
    expect(screen.getByLabelText('至少保留一段')).toBeDisabled();
  });

  it('加一段：在末尾追加空文案', async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<RandomTextsEditor texts={['白天']} onChange={onChange} />);
    await user.click(screen.getByRole('button', {name: '加一段'}));
    expect(onChange).toHaveBeenCalledWith(['白天', '']);
  });

  it('删除中间那段：其余保序', async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(
      <RandomTextsEditor texts={['白天', '夜晚', '黄昏']} onChange={onChange} />,
    );
    await user.click(screen.getByLabelText('删除随机文案 2'));
    expect(onChange).toHaveBeenCalledWith(['白天', '黄昏']);
  });

  it('改某一段：只改那一段', async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<RandomTextsEditor texts={['白天', '夜晚']} onChange={onChange} />);
    await user.type(screen.getByLabelText('随机文案 2'), 'x');
    expect(onChange).toHaveBeenLastCalledWith(['白天', '夜晚x']);
  });
});
