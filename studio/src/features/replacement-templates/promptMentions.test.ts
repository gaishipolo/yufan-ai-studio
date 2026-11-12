import { describe, expect, it } from 'vitest';
import { findPromptMention, insertPromptReference, validateTemplatePrompt } from './promptMentions';
import type { VideoTemplateOut } from '@/shared/api/schema';

const template = {rules: {}, mediaOrder: [
  {token: '{{Mixed 1}}', type: 'video'},
  {token: '{{Mixed 2}}', type: 'image'},
  {token: '{{Mixed 3}}', type: 'image'},
]} as VideoTemplateOut;

describe('提示词引用操作', () => {
  it('仅替换光标处的 @ 查询与选区，逐字保留其他内容', () => {
    const text = ' \n参考@三视图，保留动作。\n\n ';
    const caret = text.indexOf('，');
    const mention = findPromptMention(text, caret);
    expect(mention).toEqual({start: text.indexOf('@'), end: caret, query: '三视图'});
    expect(insertPromptReference(text, mention!, '{{Mixed 3}}')).toEqual({
      prompt: text.replace('@三视图', '{{Mixed 3}}'), caret: text.indexOf('@') + '{{Mixed 3}}'.length,
    });
    expect(insertPromptReference(text, {start: 0, end: 2}, '{{Mixed 1}}').prompt)
      .toBe('{{Mixed 1}}' + text.slice(2));
  });

  it('忽略引用标记内部或跨行的 @，允许相同图片重复引用', () => {
    expect(findPromptMention('@图片\n文字', 6)).toBeNull();
    expect(findPromptMention('{{Mixed 2}}', 8)).toBeNull();
    expect(validateTemplatePrompt('编辑 {{Mixed 1}}，参考 {{Mixed 2}} 和 {{Mixed 2}}。', template)).toBe('');
  });

  it('拒绝空白、非法标记、未引用原片、未引用图片及越界', () => {
    for (const text of [' \n ', '{{Mixed 1}} {{Mixed 9}}', '{{Mixed 1}} {{Product}}', '{{Mixed 2}}', '{{Mixed 1}}', '{{Mixed 1}} {{Mixed 2}']) {
      expect(validateTemplatePrompt(text, template)).not.toBe('');
    }
  });

  it('保留场景与范围参数的既有约束，不要求原片固定在第一位', () => {
    const scene = {...template, rules: {inputMode: 'video_scene_text'}};
    expect(validateTemplatePrompt('场景 {{ScenePrompt}}', scene)).toBe('');
    expect(validateTemplatePrompt('{{ScenePrompt}} {{Mixed 2}}', scene)).not.toBe('');
    expect(validateTemplatePrompt('{{Mixed 1}} {{Mixed 2}} {{ReplacementScope}}', template)).not.toBe('');
    const switched = {...template, mediaOrder: [...template.mediaOrder!].reverse().map((item, index) => ({...item, token: `{{Mixed ${index + 1}}}`}))};
    expect(validateTemplatePrompt('{{Mixed 1}} {{Mixed 3}}', switched)).toBe('');
  });
});
