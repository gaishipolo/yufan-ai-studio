import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import { Segmented } from './Segmented';

const OPTIONS = [
  {value: 'random', label: '随机'},
  {value: 'balanced', label: '均衡'},
  {value: 'custom', label: '自定义'},
] as const;

function Harness({initial = 'balanced' as string}: {initial?: string}) {
  const [value, setValue] = useState<string>(initial);
  return (
    <Segmented
      aria-label="随机性"
      options={[...OPTIONS]}
      value={value}
      onChange={setValue}
    />
  );
}

/** 滑块的位置（`transform` 里的 px）与宽度。 */
function thumbBox(container: HTMLElement): {left: number; width: number} | null {
  const thumb = container.querySelector<HTMLElement>('span[aria-hidden="true"]');
  if (!thumb) return null;
  const left = /translateX\((-?[\d.]+)px\)/.exec(thumb.style.transform);
  return {left: left ? Number(left[1]) : Number.NaN, width: Number.parseFloat(thumb.style.width)};
}

/**
 * jsdom 不做布局，`offsetLeft/offsetWidth` 恒为 0。给按钮挂上自有属性盖掉原型
 * 上的 getter，让「按实测值定位」这段逻辑有东西可测。
 */
function stubGeometry(step = 60) {
  screen.getAllByRole('radio').forEach((el, index) => {
    Object.defineProperty(el, 'offsetLeft', {configurable: true, value: index * step});
    Object.defineProperty(el, 'offsetWidth', {configurable: true, value: step});
  });
}

describe('Segmented', () => {
  it('滑块会跟着选中项走，而不是两格各自变色', async () => {
    const user = userEvent.setup();
    const { container } = render(<Harness />);
    stubGeometry();

    await user.click(screen.getByRole('radio', {name: '自定义'}));

    // 第三格：60 × 2；宽度就是那一格自己的宽度（实测，不按 1/N 均分）
    expect(thumbBox(container)).toEqual({left: 120, width: 60});
    expect(screen.getByRole('radio', {name: '自定义'})).toHaveAttribute('aria-checked', 'true');
  });

  it('选中项不能只靠一块白底区分：滑块带淡蓝底 + 主色描边、标签用深主色', () => {
    const { container } = render(<Harness />);
    const thumb = container.querySelector<HTMLElement>('span[aria-hidden="true"]');
    // 白底压在 #f7fbfc 的槽上几乎看不见 —— 这就是「切了像没切」的根因
    expect(thumb?.className).toContain('ring-primary-line');
    expect(thumb?.className).toContain('bg-primary-soft');
    expect(screen.getByRole('radio', {name: '均衡'}).className).toContain('text-primary-deep');
    expect(screen.getByRole('radio', {name: '随机'}).className).not.toContain('text-primary-deep');
  });

  it('禁用的档位显示但点不动，滑块也不会跑过去', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const { container } = render(
      <Segmented
        aria-label="随机性"
        options={[...OPTIONS]}
        value="balanced"
        onChange={onChange}
        disabledValues={['random']}
      />,
    );
    stubGeometry();

    const random = screen.getByRole('radio', {name: '随机'});
    expect(random).toBeDisabled();
    await user.click(random);

    expect(onChange).not.toHaveBeenCalled();
    expect(thumbBox(container)?.left).toBe(0);
  });

  it('值不在选项里时不画滑块（宁可没有，也不要停在错的一格上）', () => {
    const { container } = render(
      <Segmented
        aria-label="随机性"
        options={[...OPTIONS]}
        value="stale"
        onChange={() => {}}
      />,
    );
    expect(thumbBox(container)).toBeNull();
  });

  it('系统开了「减少动态效果」时不做过渡', () => {
    const { container } = render(<Harness />);
    const thumb = container.querySelector<HTMLElement>('span[aria-hidden="true"]');
    expect(thumb?.className).toContain('motion-reduce:transition-none');
  });
});
