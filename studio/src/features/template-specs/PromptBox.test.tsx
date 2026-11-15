import { useState } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { PromptBox, type PromptChip } from './PromptBox';

const CHIPS: PromptChip[] = [
  {token: '{{slot:image_1}}', label: '主图', all: false},
  {token: '{{slot:image_2}}', label: '细节图', all: false},
  {token: '{{slot:pack_1*}}', label: '封装（全部）', all: true},
];

/* 候选面板里的项和下方的 chip 按钮同名（都是「主图」），用容器区分：
   候选项带「单个/全部」标记，chip 按钮不带。 */
function candidate(name: RegExp) {
  return screen.getByRole('button', {name: new RegExp(`${name.source}.*(单个|全部)`)});
}

/** 受控外壳：与真实用法一致（值由父组件持有）。 */
function Harness({chips = CHIPS}: {chips?: PromptChip[]}) {
  const [text, setText] = useState('');
  return (
    <>
      <PromptBox value={text} chips={chips} onChange={setText} onInsert={setText} />
      <output data-testid="value">{text}</output>
    </>
  );
}

describe('提示词框的 @ 提及', () => {
  it('打 @ 弹出候选面板', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const box = screen.getByRole('textbox');
    await user.click(box);
    await user.type(box, '@');

    expect(screen.getByText('选择要引用的槽位')).toBeInTheDocument();
    expect(candidate(/主图/)).toBeInTheDocument();
  });

  it('继续打字过滤候选', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const box = screen.getByRole('textbox');
    await user.click(box);
    await user.type(box, '@细节');

    expect(screen.getByText('匹配「细节」')).toBeInTheDocument();
    expect(candidate(/细节图/)).toBeInTheDocument();
    expect(screen.queryByRole('button', {name: /主图.*(单个|全部)/})).not.toBeInTheDocument();
  });

  it('鼠标点候选：@查询词 整段换成 token', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const box = screen.getByRole('textbox');
    await user.click(box);
    await user.type(box, '把 @主');
    await user.click(candidate(/主图/));

    expect(screen.getByTestId('value')).toHaveTextContent('把 {{slot:image_1}}');
    // 选完面板收起
    expect(screen.queryByText(/匹配|选择要引用/)).not.toBeInTheDocument();
  });

  it('键盘：↑↓ 选候选，Enter 确认', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const box = screen.getByRole('textbox');
    await user.click(box);
    await user.type(box, '@');
    await user.keyboard('{ArrowDown}');
    await user.keyboard('{Enter}');

    // 默认高亮第一项，↓ 之后是第二项
    expect(screen.getByTestId('value')).toHaveTextContent('{{slot:image_2}}');
  });

  it('Escape 收起面板但不改动文本', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const box = screen.getByRole('textbox');
    await user.click(box);
    await user.type(box, '@主');
    await user.keyboard('{Escape}');

    expect(screen.queryByText(/匹配|选择要引用/)).not.toBeInTheDocument();
    expect(screen.getByTestId('value')).toHaveTextContent('@主');
  });

  it('没有候选时（输入邮箱）不弹面板', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const box = screen.getByRole('textbox');
    await user.click(box);
    await user.type(box, '联系 a@b');

    expect(screen.queryByText(/匹配|选择要引用/)).not.toBeInTheDocument();
  });

  it('下方的 chip 按钮照旧能插入引用（不破坏原功能）', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole('button', {name: '主图'}));

    expect(screen.getByTestId('value')).toHaveTextContent('{{slot:image_1}}');
  });
});
