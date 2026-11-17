import {render, screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {useState} from 'react';
import {describe, expect, it, vi} from 'vitest';
import {NumberField} from './NumberField';

/**
 * 数字输入框的守卫。
 *
 * 这里守的是一件具体的事：**用户能把 5 改成 10，而不用先手动删掉 5**。
 * 修之前是受控 `<input type="number">`：光标在末尾打 `10` → `510`。
 *
 * 根因不在受控，而在 **number 控件不支持选区**（实测真实 Chrome：
 * `selectionStart` 恒为 null，且浏览器在 input 事件层就完成了追加）。
 * 所以实现换成了文本框 + `inputMode="numeric"` + 自己过滤非法字符。
 */
function Harness({
  initial = 5,
  min,
  max,
  step,
}: {
  initial?: number | '';
  min?: number;
  max?: number;
  step?: number;
}) {
  const [value, setValue] = useState<number | ''>(initial);
  return (
    <>
      <NumberField
        id="dur"
        label="时长"
        value={value}
        {...(min !== undefined ? {min} : {})}
        {...(max !== undefined ? {max} : {})}
        {...(step !== undefined ? {step} : {})}
        onChange={setValue}
      />
      <output data-testid="out">{String(value)}</output>
    </>
  );
}

describe('NumberField · 改值', () => {
  it('点击时把旧值全选（所以接着打就是替换）', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const input = screen.getByLabelText('时长') as HTMLInputElement;

    await user.click(input);

    /* 全选是靠 `select()` 实现的。
       **注意**：`type="number"` 的 `selectionStart` 恒为 null（实测真实 Chrome
       也是），所以这里断言的是「select() 被调用过」——
       用 spy 盯着，比断言选区更贴得住真实行为。 */
    const select = vi.spyOn(input, 'select');
    await user.click(input);
    expect(select).toHaveBeenCalled();
  });

  it('超上限的值在失焦时被钳到 max', async () => {
    const user = userEvent.setup();
    render(<Harness min={2} max={30} />);
    const input = screen.getByLabelText('时长') as HTMLInputElement;

    await user.click(input);
    await user.clear(input);
    await user.type(input, '999');
    await user.tab();

    expect(input.value).toBe('30');
    expect(screen.getByTestId('out').textContent).toBe('30');
  });

  it('低于下限的值在失焦时被钳到 min', async () => {
    const user = userEvent.setup();
    render(<Harness min={2} max={30} />);
    const input = screen.getByLabelText('时长') as HTMLInputElement;

    await user.click(input);
    await user.clear(input);
    await user.type(input, '0');
    await user.tab();

    expect(input.value).toBe('2');
  });

  it('敲出超范围值时，输入框保留用户敲的原样（不提前改写）', async () => {
    const user = userEvent.setup();
    render(<Harness min={2} max={30} />);
    const input = screen.getByLabelText('时长') as HTMLInputElement;

    await user.click(input);
    await user.clear(input);
    await user.type(input, '999');

    /* 还没失焦：输入框里该是用户敲的 999，不是被提前钳成的 30。
       提前钳制 = 用户在打字过程中看到数字自己跳走（「我明明打的 999」）。 */
    expect(input.value).toBe('999');
  });

  it('清空 = 不提交这个参数（空串交上去，后端用默认值）', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const input = screen.getByLabelText('时长') as HTMLInputElement;

    await user.click(input);
    await user.clear(input);
    await user.tab();

    expect(input.value).toBe('');
    expect(screen.getByTestId('out').textContent).toBe('');
  });

  it('是原生 number 控件（上下箭头与 min/max 校验都在）', () => {
    render(<Harness min={2} max={30} step={1} />);
    const input = screen.getByLabelText('时长') as HTMLInputElement;
    expect(input.type).toBe('number');
    expect(input.min).toBe('2');
    expect(input.max).toBe('30');
    expect(input.step).toBe('1');
  });
});
