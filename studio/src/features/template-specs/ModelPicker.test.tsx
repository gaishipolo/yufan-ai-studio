import {render, screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {useState} from 'react';
import {describe, expect, it} from 'vitest';
import type {ModelSpec} from '@/entities/model/model/types';
import {ModelPicker} from './ModelPicker';

const models: ModelSpec[] = [
  {id: 'libtv', label: 'LibTV 换脸'} as ModelSpec,
  {id: 'bailian', label: '百炼 替换'} as ModelSpec,
  {id: 'agnes', label: 'Agnes 图生图'} as ModelSpec,
];

function Harness({initial = [] as string[]}) {
  const [value, setValue] = useState<string[]>(initial);
  return (
    <>
      <ModelPicker models={models} value={value} onChange={setValue} />
      <output data-testid="out">{value.join(',')}</output>
    </>
  );
}

describe('ModelPicker · 勾选顺序即默认序', () => {
  it('展开后能勾选多款，勾选顺序保留', async () => {
    const user = userEvent.setup();
    render(<Harness />);

    await user.click(screen.getByLabelText('模型'));
    await user.click(screen.getByLabelText('百炼 替换'));
    await user.click(screen.getByLabelText('Agnes 图生图'));

    expect(screen.getByTestId('out').textContent).toBe('bailian,agnes');
  });

  it('第一个勾的标「默认」，后面的不标', async () => {
    const user = userEvent.setup();
    render(<Harness initial={['bailian', 'agnes']} />);

    await user.click(screen.getByLabelText('模型'));

    // 两个已勾项，只有第一个标默认
    const defaults = screen.getAllByText('默认');
    expect(defaults).toHaveLength(1);
    // 默认标在「百炼 替换」这一行
    expect(defaults[0].closest('li')?.textContent).toContain('百炼 替换');
  });

  it('取消中间那款，其余保持相对顺序（默认模型不变）', async () => {
    const user = userEvent.setup();
    render(<Harness initial={['bailian', 'agnes', 'libtv']} />);

    await user.click(screen.getByLabelText('模型'));
    await user.click(screen.getByLabelText('Agnes 图生图'));

    // 取消的是中间项，默认仍是 bailian
    expect(screen.getByTestId('out').textContent).toBe('bailian,libtv');
    const defaults = screen.getAllByText('默认');
    expect(defaults).toHaveLength(1);
    expect(defaults[0].closest('li')?.textContent).toContain('百炼 替换');
  });

  it('收起态显示默认模型的名字', () => {
    render(<Harness initial={['bailian', 'agnes']} />);

    const button = screen.getByLabelText('模型');
    expect(button.textContent).toContain('百炼 替换');
    expect(button.textContent).toContain('等 2 款');
  });

  it('没勾时收起态是提示文案', () => {
    render(<Harness />);
    expect(screen.getByLabelText('模型').textContent).toContain('选择模型');
  });

  it('已勾的排在列表前面（顺序即默认序，一眼看得见）', async () => {
    const user = userEvent.setup();
    render(<Harness initial={['agnes', 'bailian']} />);

    await user.click(screen.getByLabelText('模型'));

    const items = screen.getAllByRole('checkbox');
    // 第一个复选框对应「Agnes 图生图」（默认），第二个是「百炼 替换」
    expect(items[0].getAttribute('aria-label')).toBe('Agnes 图生图');
    expect(items[1].getAttribute('aria-label')).toBe('百炼 替换');
  });
});

describe('ModelPicker · 数量上限', () => {
  it('勾到上限后不再接受新的，并给出明说', async () => {
    const user = userEvent.setup();
    /* 造 12 款模型：够触发上限 */
    const many: ModelSpec[] = Array.from({length: 12}, (_, i) => ({
      id: `m${i}`,
      label: `模型 ${i}`,
    })) as ModelSpec[];
    const picked: string[] = [];
    render(
      <ModelPicker
        models={many}
        value={picked}
        onChange={values => picked.splice(0, picked.length, ...values)}
      />,
    );

    await user.click(screen.getByLabelText('模型'));
    /* 先勾满 10 款 */
    for (let i = 0; i < 10; i += 1) {
      await user.click(screen.getByLabelText(`模型 ${i}`));
    }
    expect(picked).toHaveLength(10);

    /* 第 11 款：不该被接受 */
    await user.click(screen.getByLabelText('模型 10'));
    expect(picked).toHaveLength(10);
    expect(screen.getByText(/最多选 10 款模型/)).toBeTruthy();
  });
});
