import { describe, expect, it } from 'vitest';
import { readPromptDom, getPromptSelection, setPromptSelection, atomicPromptRange } from './promptEditorDom';

function fixture() {
  const root = document.createElement('div');
  root.contentEditable = 'true';
  root.append(document.createTextNode('  编辑 '));
  const chip = document.createElement('span');
  chip.dataset.promptToken = '{{Mixed 1}}';
  chip.contentEditable = 'false';
  chip.textContent = '视频 1（缩略图和文件名不属于原文）';
  root.append(chip, document.createTextNode('，保留动作。\n\n '));
  document.body.append(root);
  return root;
}

describe('画布提示词 DOM 与原文', () => {
  it('引用标签还原标记，缩略图和文件名不混入正文，空白逐字保留', () => {
    const root = fixture();
    expect(readPromptDom(root)).toBe('  编辑 {{Mixed 1}}，保留动作。\n\n ');
    root.remove();
  });

  it('引用前后与跨引用选区按原文字符位置映射', () => {
    const root = fixture();
    for (const range of [{start: 2, end: 2}, {start: 5, end: 16}, {start: 16, end: 16}, {start: 0, end: 28}]) {
      const max = readPromptDom(root).length;
      const bounded = {start: Math.min(range.start, max), end: Math.min(range.end, max)};
      setPromptSelection(root, bounded);
      expect(getPromptSelection(root)).toEqual(bounded);
    }
    root.remove();
  });

  it('Backspace 与 Delete 将引用作为整体删除，普通文字仍按字符处理', () => {
    const text = 'A{{Mixed 2}}B';
    expect(atomicPromptRange(text, {start: 12, end: 12}, 'backward')).toEqual({start: 1, end: 12});
    expect(atomicPromptRange(text, {start: 1, end: 1}, 'forward')).toEqual({start: 1, end: 12});
    expect(atomicPromptRange(text, {start: 1, end: 1}, 'backward')).toEqual({start: 0, end: 1});
  });

  it('浏览器插入的换行与块级节点不会被拼成一行', () => {
    const root = document.createElement('div');
    root.innerHTML = '第一行<div>第二行<br>末行</div><div><br></div>';
    expect(readPromptDom(root)).toBe('第一行\n第二行\n末行\n');
    root.innerHTML = '第一行<div><br></div><div>第三行</div>';
    expect(readPromptDom(root)).toBe('第一行\n\n第三行');
  });

  it('删除普通文字时以完整字形为单位，不截断 emoji 或组合字符', () => {
    const text = 'A👩‍💻B';
    const end = text.indexOf('B');
    expect(atomicPromptRange(text, {start: end, end}, 'backward')).toEqual({start: 1, end});
    expect(atomicPromptRange(text, {start: 1, end: 1}, 'forward')).toEqual({start: 1, end});
  });
});
