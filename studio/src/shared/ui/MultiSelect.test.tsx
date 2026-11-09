import { useState } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MultiSelect, type MultiSelectOption } from './MultiSelect';

const OPTIONS: MultiSelectOption[] = [
  { value: 't1', label: '音乐' },
  { value: 't2', label: '音效' },
];

/** 受控组件的真实用法：选中结果要由父级保存下来，否则下一次渲染就丢了。 */
function Harness({
  onCreate,
  options = OPTIONS,
}: {
  onCreate?: (name: string) => Promise<string | null>;
  options?: MultiSelectOption[];
}) {
  const [value, setValue] = useState<string[]>([]);
  return (
    <>
      <MultiSelect
        options={options}
        value={value}
        onChange={setValue}
        ariaLabel="标签"
        emptyText="还没有标签"
        onCreate={onCreate}
      />
      <output data-testid="picked">{value.join(',')}</output>
    </>
  );
}

describe('MultiSelect', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });
  it('不传 onCreate 就不出现新建行：没权限的人不该看到注定失败的输入框', async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(screen.getByRole('button', { name: '标签' }));

    expect(screen.getByRole('checkbox', { name: '音乐' })).toBeInTheDocument();
    expect(screen.queryByPlaceholderText('新建并选中')).not.toBeInTheDocument();
  });

  it('就地新建：回车提交，建成后自动选中并清空输入', async () => {
    const user = userEvent.setup();
    const onCreate = vi.fn(async () => 't9');
    render(<Harness onCreate={onCreate} />);

    await user.click(screen.getByRole('button', { name: '标签' }));
    const input = screen.getByPlaceholderText('新建并选中');
    await user.type(input, '场景{Enter}');

    expect(onCreate).toHaveBeenCalledWith('场景');
    expect(screen.getByTestId('picked')).toHaveTextContent('t9');
    // 建成后输入框清空，收起态直接显示标签名（选项还没回流也能读出来）
    expect(input).toHaveValue('');
    expect(screen.getByRole('button', { name: '标签' })).toHaveTextContent('场景');
  });

  it('没建成（返回 null）时不清空输入、不选中：改一个字就能重试', async () => {
    const user = userEvent.setup();
    const onCreate = vi.fn(async () => null);
    render(<Harness onCreate={onCreate} />);

    await user.click(screen.getByRole('button', { name: '标签' }));
    const input = screen.getByPlaceholderText('新建并选中');
    await user.type(input, '重名{Enter}');

    expect(input).toHaveValue('重名');
    expect(screen.getByTestId('picked')).toBeEmptyDOMElement();
  });

  it('空白输入不提交：只按回车不该发请求', async () => {
    const user = userEvent.setup();
    const onCreate = vi.fn(async () => 't9');
    render(<Harness onCreate={onCreate} />);

    await user.click(screen.getByRole('button', { name: '标签' }));
    await user.type(screen.getByPlaceholderText('新建并选中'), '   {Enter}');

    expect(onCreate).not.toHaveBeenCalled();
  });

  it('不抛错：onCreate 抛异常时保持输入，界面不炸', async () => {
    const user = userEvent.setup();
    const onCreate = vi.fn(async () => {
      throw new Error('网络断了');
    });
    render(<Harness onCreate={onCreate} />);

    await user.click(screen.getByRole('button', { name: '标签' }));
    const input = screen.getByPlaceholderText('新建并选中');
    await user.type(input, '室内{Enter}');

    expect(input).toHaveValue('室内');
    expect(screen.getByTestId('picked')).toBeEmptyDOMElement();
  });

  it('一个选项都没有时，空态与新建行同时出现', async () => {
    const user = userEvent.setup();
    render(<Harness onCreate={async () => 't9'} options={[]} />);

    await user.click(screen.getByRole('button', { name: '标签' }));

    expect(screen.getByText('还没有标签')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('新建并选中')).toBeInTheDocument();
  });

  /* 浮层方向：批量栏贴在视口底部，朝下展开会整块落到屏幕外（用户只看到一条边）。 */
  describe('浮层方向', () => {
    /** 把触发器的位置钉在视口里：`bottom` 之后剩多少空间由 innerHeight 决定。 */
    function placeTriggerAt(bottom: number, innerHeight: number) {
      Object.defineProperty(window, 'innerHeight', { value: innerHeight, configurable: true });
      vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue({
        bottom,
        top: bottom - 32,
        height: 32,
        left: 0,
        right: 200,
        width: 200,
        x: 0,
        y: bottom - 32,
        toJSON: () => ({}),
      } as DOMRect);
    }

    const popoverOf = () => {
      const trigger = screen.getByRole('button', { name: '标签' });
      const id = trigger.getAttribute('aria-controls') ?? '';
      return document.getElementById(id);
    };

    it('下方够高就朝下展开', async () => {
      const user = userEvent.setup();
      placeTriggerAt(120, 800);
      render(<Harness />);

      await user.click(screen.getByRole('button', { name: '标签' }));

      expect(popoverOf()).toHaveAttribute('data-placement', 'bottom');
    });

    it('贴视口底部时朝上展开（批量栏的场景）', async () => {
      const user = userEvent.setup();
      placeTriggerAt(760, 800);
      render(<Harness />);

      await user.click(screen.getByRole('button', { name: '标签' }));

      expect(popoverOf()).toHaveAttribute('data-placement', 'top');
      // 朝上时不能还留着向下展开的定位类
      expect(popoverOf()?.className).not.toContain('top-[calc(100%+4px)]');
    });
  });
});
