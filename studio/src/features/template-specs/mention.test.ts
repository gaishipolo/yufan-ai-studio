import { describe, expect, it } from 'vitest';
import {
  applyMention,
  detectMention,
  filterMentionChips,
} from './mention';

describe('提示词 @ 提及', () => {
  it('光标在 @ 之后：认得出这是提及', () => {
    const hit = detectMention('把 @主', 4);
    expect(hit).not.toBeNull();
    expect(hit?.start).toBe(2);
    expect(hit?.end).toBe(4);
    expect(hit?.query).toBe('主');
  });

  it('只有 @ 没有查询词也算（刚打完 @ 就要出候选）', () => {
    const hit = detectMention('把 @', 3);
    expect(hit?.query).toBe('');
    expect(hit?.start).toBe(2);
  });

  it('@ 后跟空格是正文（at someone），不是提及', () => {
    expect(detectMention('发邮件给 @bob ', 12)).toBeNull();
    expect(detectMention('看 @ 首页', 6)).toBeNull();
  });

  it('邮箱 / 域名里的 @ 不触发', () => {
    // 打出域名形状了才当地址 —— 只看「@ 前面有没有词字符」会误伤
    // 「cinematic shot @」这种正经用法（提示词里写英文太常见了）
    expect(detectMention('联系 a@b.com', 11)).toBeNull();
    expect(detectMention('site.com@', 9)).toBeNull();
  });

  it('英文后面打 @ 仍然是一次提及（用户报过的坑）', () => {
    // "cinematic handheld shot@" 共 24 个字符，@ 在下标 23。
    // **@ 前面没有空格** —— 用户报的就是这种：一串英文紧跟着打 @。
    const hit = detectMention('cinematic handheld shot@', 24);
    expect(hit?.query).toBe('');
    expect(hit?.start).toBe(23);

    // 打完 @ 继续过滤候选，候选面板不该被收掉
    const filtering = detectMention('cinematic handheld shot@原', 25);
    expect(filtering?.query).toBe('原');
    expect(filtering?.start).toBe(23);
  });

  it('英文 + @ 选出来的 token 替换的是 @ 之后那段', () => {
    const text = 'shot @原';
    const range = detectMention(text, 7)!;
    const result = applyMention(text, range, '{{slot:role_1}}');
    expect(result.text).toBe('shot {{slot:role_1}}');
  });

  it('连打两个 @：后一个 @ 开启新的一段查询', () => {
    // `@图@像` 不是一次提及 —— 第二个 @ 是新的起点，跟大多数输入框的 @ 提及一致。
    const hit = detectMention('@图@像', 4);
    expect(hit?.start).toBe(2);
    expect(hit?.query).toBe('像');
  });

  it('查询词里不能有花括号（提示词正文里写了 {}）', () => {
    expect(detectMention('@{x}', 4)).toBeNull();
  });

  it('光标不在 @ 之后就不触发', () => {
    // 光标在 @ 前面：还没开始输入
    expect(detectMention('@abc', 0)).toBeNull();
  });

  it('候选按展示名过滤，命中不了就空列表', () => {
    const chips = [
      {label: '主图', token: '{{slot:image_1}}'},
      {label: '细节图', token: '{{slot:image_2}}'},
    ];
    expect(filterMentionChips(chips, '主').map(c => c.label)).toEqual(['主图']);
    expect(filterMentionChips(chips, '图').map(c => c.label)).toEqual(['主图', '细节图']);
    expect(filterMentionChips(chips, '')).toHaveLength(2);
    expect(filterMentionChips(chips, '不存在')).toHaveLength(0);
  });

  it('选中后整段 @查询词 换成 token，光标停在 token 之后', () => {
    const text = '把 @主 换成';
    const range = detectMention(text, 4)!;
    const result = applyMention(text, range, '{{slot:image_1}}');
    expect(result.text).toBe('把 {{slot:image_1}} 换成');
    expect(result.caret).toBe('把 {{slot:image_1}}'.length);
  });

  it('替换后不留多余空格：紧贴中文时补一个空格', () => {
    const text = '把@主换掉';
    const range = detectMention(text, 3)!;
    const result = applyMention(text, range, '{{slot:image_1}}');
    expect(result.text).toBe('把 {{slot:image_1}} 换掉');
  });

  it('提示词开头的 @ 不补前导空格', () => {
    const text = '@主图';
    const range = detectMention(text, 3)!;
    expect(applyMention(text, range, '{{slot:image_1}}').text).toBe('{{slot:image_1}}');
  });
});
