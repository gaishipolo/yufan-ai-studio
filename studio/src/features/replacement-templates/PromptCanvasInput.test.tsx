import { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { PromptCanvasInput } from './PromptCanvasInput';
import { readPromptDom, setPromptSelection } from './promptEditorDom';

vi.mock('@/shared/lib/remoteBlob', () => ({useRemoteBlob: () => ({data: undefined})}));
const original = '  编辑 {{Mixed 1}}，参考 {{Mixed 2}}。\n\n';
const media = [{id: 'video', token: '{{Mixed 1}}', name: '原片', type: 'video'}, {id: 'image', token: '{{Mixed 2}}', name: '正面图', type: 'image', url: '/front'}];

function Harness() {
  const [value, setValue] = useState(original);
  return <><PromptCanvasInput id="test-editor" value={value} media={media} disabled={false} describedBy="hint"
    onChange={setValue} onSelection={() => {}} onKeyDown={() => {}} onComposing={() => {}} onPreview={() => {}} onFiles={() => {}} /><output data-testid="raw">{value}</output></>;
}

describe('画布引用编辑输入', () => {
  it('原子删除引用标签，并能撤销和重做且保留全部其他文字', () => {
    render(<Harness />);
    const input = screen.getByRole('textbox');
    const end = original.indexOf('{{Mixed 2}}') + '{{Mixed 2}}'.length;
    setPromptSelection(input, {start: end, end});
    fireEvent.keyDown(input, {key: 'Backspace'});
    expect(screen.getByTestId('raw').textContent).toBe(original.replace('{{Mixed 2}}', ''));
    expect(readPromptDom(input)).toBe(original.replace('{{Mixed 2}}', ''));
    fireEvent.keyDown(input, {key: 'z', ctrlKey: true});
    expect(screen.getByTestId('raw').textContent).toBe(original);
    fireEvent.keyDown(input, {key: 'z', ctrlKey: true, shiftKey: true});
    expect(screen.getByTestId('raw').textContent).toBe(original.replace('{{Mixed 2}}', ''));
  });

  it('粘贴按纯文本处理，引用渲染后仍逐字保存空白、换行与文字', () => {
    render(<Harness />);
    const input = screen.getByRole('textbox');
    const pasted = ' \n<b>普通文字</b> {{Mixed 2}}\n\n';
    setPromptSelection(input, {start: 0, end: original.length});
    fireEvent.paste(input, {clipboardData: {files: [], getData: (kind: string) => kind === 'text/plain' ? pasted : '<script>bad()</script>'}});
    expect(screen.getByTestId('raw').textContent).toBe(pasted);
    expect(readPromptDom(input)).toBe(pasted);
    expect(input.querySelector('script')).toBeNull();
    expect(input.querySelector('b')).toBeNull();
  });

  it('复制引用时写入原始标记，不复制缩略图名称；Enter 只插入换行', () => {
    render(<Harness />);
    const input = screen.getByRole('textbox');
    const start = original.indexOf('{{Mixed 2}}');
    setPromptSelection(input, {start, end: start + '{{Mixed 2}}'.length});
    const setData = vi.fn();
    fireEvent.copy(input, {clipboardData: {setData}});
    expect(setData).toHaveBeenCalledWith('text/plain', '{{Mixed 2}}');
    setPromptSelection(input, {start: original.length, end: original.length});
    fireEvent.keyDown(input, {key: 'Enter'});
    expect(screen.getByTestId('raw').textContent).toBe(original + '\n');
  });

  it('中文组合输入期间不重绘或提交，结束时一次保存中文原文', () => {
    render(<Harness />);
    const input = screen.getByRole('textbox');
    fireEvent.compositionStart(input);
    const next = original + '中文';
    input.replaceChildren(document.createTextNode(next));
    setPromptSelection(input, {start: next.length, end: next.length});
    fireEvent.input(input, {isComposing: true});
    expect(screen.getByTestId('raw').textContent).toBe(original);
    fireEvent.compositionEnd(input);
    expect(screen.getByTestId('raw').textContent).toBe(next);
    expect(readPromptDom(input)).toBe(next);
  });
});
