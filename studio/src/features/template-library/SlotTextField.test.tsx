import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { SlotTextField } from './SlotTextField';

/**
 * 使用者改写「未选时文案」时，作者写的那段话里可能有对别的槽位的引用。
 * textarea 只能显示成裸的 `{{Mixed 1}}` —— 使用者看到的是一串看不懂的花括号。
 * 换成编辑器后，引用画成胶囊。
 */
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
});
