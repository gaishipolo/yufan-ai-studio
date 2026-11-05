import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Pager } from './Pager';

describe('Pager', () => {
  it('只有一页时不占位置', () => {
    const { container } = render(<Pager page={1} totalPages={1} onChange={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('正常页码不回调（否则会自己触发一次多余的取数）', () => {
    const onChange = vi.fn();
    render(<Pager page={2} totalPages={3} onChange={onChange} />);

    expect(screen.getByText('第 2 / 3 页')).toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });

  /* FE4-06：删掉第 3 页最后一条后 total 变小，page 却还停在 3。
     只把显示归一化成「第 2 / 2 页」是不够的 —— 调用方仍会按第 3 页取数，
     后端回空数组，界面就成了「有页码、没数据」。必须回写一次。 */
  it('页码越界时回写边界值', () => {
    const onChange = vi.fn();
    render(<Pager page={3} totalPages={2} onChange={onChange} />);

    expect(onChange).toHaveBeenCalledWith(2);
    expect(screen.getByText('第 2 / 2 页')).toBeInTheDocument();
  });

  it('总数归零（删空）时收回到第 1 页', () => {
    const onChange = vi.fn();
    render(<Pager page={2} totalPages={1} onChange={onChange} />);

    expect(onChange).toHaveBeenCalledWith(1);
  });

  /* totalPages = 0 是调用方的「还不知道总数」（加载中 / 请求失败）。
     这时不能回收，否则一次网络抖动就把用户从第 3 页悄悄踢回第 1 页。 */
  it('还不知道总数时不回收页码', () => {
    const onChange = vi.fn();
    const { container } = render(<Pager page={3} totalPages={0} onChange={onChange} />);

    expect(onChange).not.toHaveBeenCalled();
    expect(container).toBeEmptyDOMElement();
  });
});
