import { render, screen } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { SlotTextField } from './SlotTextField';

/**
 * 使用者改写「未选时文案」时，作者写的那段话里可能有对别的槽位的引用。
 * textarea 只能显示成裸的 `{{Mixed 1}}` —— 使用者看到的是一串看不懂的花括号。
 * 换成编辑器后，引用画成胶囊。
 */
/** 受控外壳：value 由组件自己持有（编辑器是受控的，父组件不更新 value 就会一直重绘）。 */
function Harness({onChange}: {onChange?: (value: string) => void}) {
  const [text, setText] = useState('参考 {{Mixed 1}} 的构图');
  return (
    <>
      <SlotTextField
        value={text}
        onChange={next => {
          setText(next);
          onChange?.(next);
        }}
        chips={[{token: '{{Mixed 1}}', label: '正面图', all: false, kind: 'image' as const}]}
      />
      <output data-testid="value">{text}</output>
    </>
  );
}

describe('使用者改写「未选时文案」的输入框', () => {
  it('里面的引用画成胶囊，不再是裸的花括号', () => {
    render(
      <SlotTextField
        value="参考 {{Mixed 1}} 的构图"
        onChange={() => {}}
        placeholder="写这一格没素材时提示词怎么说"
      />,
    );
    const box = screen.getByRole('textbox', { name: '改提示词' });
    expect(box.querySelector('[data-token]')).not.toBeNull();
  });

  it('空的时候有占位提示（contenteditable 没有原生 placeholder）', () => {
    render(<SlotTextField value="" onChange={() => {}} placeholder="写这一格没素材时提示词怎么说" />);
    const box = screen.getByRole('textbox', { name: '改提示词' });
    expect(box).toHaveAttribute('data-placeholder', '写这一格没素材时提示词怎么说');
  });
  it('胶囊上写的是那个槽位的名字，不是 {{Mixed 1}}', () => {
    render(
      <SlotTextField
        value="参考 {{Mixed 1}} 的构图"
        onChange={() => {}}
        chips={[{ token: '{{Mixed 1}}', label: '正面图', all: false, kind: 'image' as const }]}
      />,
    );
    const pill = screen.getByRole('textbox', { name: '改提示词' }).querySelector('[data-token]');
    expect(pill).toHaveTextContent('正面图');
  });

  it('使用者新敲的引用被拒（作者写好的那些保持原样）', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    const box = screen.getByRole('textbox', {name: '改提示词'});
    box.focus();
    /* 光标放到末尾（作者那段的最后） */
    const range = document.createRange();
    range.selectNodeContents(box.lastChild ?? box);
    range.collapse(false);
    const sel = window.getSelection()!;
    sel.removeAllRanges();
    sel.addRange(range);
    await user.keyboard(' {{Mixed 2}}');
    /* 新敲的引用一个都不许提交（作者写好的那个照旧） */
    expect(onChange).not.toHaveBeenCalledWith(expect.stringContaining('{{Mixed 2}}'));
    /* 关键属性：**完整的新引用不能进提交**（后端会把它当引用处理）。 */
    expect(screen.getByTestId('value').textContent).not.toContain('{{Mixed 2}}');
  });

  it('作者原文换了，允许保留的引用跟着换（不再靠「组件什么时候挂载」）', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const { rerender } = render(
      <SlotTextField
        value="参考 {{Mixed 1}} 的构图"
        onChange={onChange}
        authorText="参考 {{Mixed 1}} 的构图"
        chips={[{token: '{{Mixed 1}}', label: '正面图', all: false, kind: 'image' as const}]}
      />,
    );
    const box = screen.getByRole('textbox', {name: '改提示词'});
    box.focus();
    const range = document.createRange();
    range.selectNodeContents(box.lastChild ?? box);
    range.collapse(false);
    const sel = window.getSelection()!;
    sel.removeAllRanges();
    sel.addRange(range);
    await user.keyboard(' {{Mixed 2}}');
    // 原文里只有 {{Mixed 1}} → {{Mixed 2}} 是新敲的，被拒
    expect(onChange).not.toHaveBeenCalledWith(expect.stringContaining('{{Mixed 2}}'));

    // 换成作者的原文里**有** {{Mixed 2}} → 同样的输入就该放行
    onChange.mockClear();
    rerender(
      <SlotTextField
        value="参考 {{Mixed 2}} 的构图"
        onChange={onChange}
        authorText="参考 {{Mixed 2}} 的构图"
        chips={[{token: '{{Mixed 2}}', label: '正面图', all: false, kind: 'image' as const}]}
      />,
    );
    await user.keyboard(' {{Mixed 2}}');
    expect(onChange).toHaveBeenCalledWith(expect.stringContaining('{{Mixed 2}}'));
  });
});
