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
function Harness({ options = OPTIONS }: { options?: MultiSelectOption[] }) {
  const [value, setValue] = useState<string[]>([]);
  return (
    <>
      <MultiSelect
        options={options}
        value={value}
        onChange={setValue}
        ariaLabel="标签"
        emptyText="还没有标签"
      />
      <output data-testid="picked">{value.join(',')}</output>
    </>
  );
}

describe('MultiSelect', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });
  it('**没有就地新建入口**：标签词表统一在「标签管理」里建', async () => {
    /* 守卫：就地新建只能建一级（挂不上维度），建出来还得再挪一次层级。
       取消这个能力后，任何调用点都不该再把新建入口摆出来。 */
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(screen.getByRole('button', { name: '标签' }));

    expect(screen.getByRole('checkbox', { name: '音乐' })).toBeInTheDocument();
    expect(screen.queryByPlaceholderText('新建并选中')).not.toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '新建' })).not.toBeInTheDocument();
  });

  it('一个选项都没有时只出现空态（没有新建行陪着）', async () => {
    const user = userEvent.setup();
    render(<Harness options={[]} />);

    await user.click(screen.getByRole('button', { name: '标签' }));

    expect(screen.getByText('还没有标签')).toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
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
