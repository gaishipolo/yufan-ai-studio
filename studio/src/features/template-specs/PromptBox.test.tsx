import { useState } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
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
    await user.type(box, '联系 a@b.com');

    expect(screen.queryByText(/匹配|选择要引用/)).not.toBeInTheDocument();
  });

  it('英文后面打 @ 照样能唤起候选面板（用户报过的坑）', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const box = screen.getByRole('textbox');
    await user.click(box);
    // **@ 前面没有空格** —— 用户报的就是这种：一串英文紧跟着打 @
    await user.type(box, 'cinematic handheld shot@');

    expect(screen.getByText('选择要引用的槽位')).toBeInTheDocument();
    expect(candidate(/主图/)).toBeInTheDocument();
  });

  it('英文后面打 @ 再继续过滤，候选也跟着筛', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const box = screen.getByRole('textbox');
    await user.click(box);
    await user.type(box, 'cinematic shot@主');

    expect(screen.getByText('匹配「主」')).toBeInTheDocument();
    expect(candidate(/主图/)).toBeInTheDocument();
  });

  it('下方的 chip 按钮照旧能插入引用（不破坏原功能）', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole('button', {name: '主图'}));

    expect(screen.getByTestId('value')).toHaveTextContent('{{slot:image_1}}');
  });
});

/**
 * 浮层方向：提示词框在模板封装页的右栏最下面，朝下展开会整块掉到视口外 ——
 * 用户只看到输入框闪一下、什么也没出现。空间不够就得翻到上面去。
 */
describe('候选面板的方向', () => {
  /** 把输入框钉在视口里的某个位置：下方还剩多少空间由 innerHeight 决定。 */
  function placeBoxAt(bottom: number, innerHeight: number) {
    Object.defineProperty(window, 'innerHeight', { value: innerHeight, configurable: true });
    vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue({
      bottom,
      top: bottom - 60,
      height: 100,
      left: 0,
      right: 400,
      width: 400,
      x: 0,
      y: bottom - 60,
      toJSON: () => ({}),
    } as DOMRect);
  }

  const panel = () => screen.getByText('选择要引用的槽位').closest('[data-placement]');

  it('下方空间不够时朝上展开（提示词框贴着视口底边的场景）', async () => {
    const user = userEvent.setup();
    placeBoxAt(760, 800);
    render(<Harness />);
    const box = screen.getByRole('textbox');
    await user.click(box);
    await user.type(box, '@');

    expect(panel()).toHaveAttribute('data-placement', 'top');
    // 朝上时不能还留着朝下的定位类，否则会同时挂两条定位
    expect(panel()?.className).not.toContain('top-[calc(100%+4px)]');
  });

  it('下方够高就朝下展开', async () => {
    const user = userEvent.setup();
    placeBoxAt(120, 800);
    render(<Harness />);
    const box = screen.getByRole('textbox');
    await user.click(box);
    await user.type(box, '@');

    expect(panel()).toHaveAttribute('data-placement', 'bottom');
  });
});
