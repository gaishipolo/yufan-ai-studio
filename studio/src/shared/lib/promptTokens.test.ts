import { describe, expect, it } from 'vitest';
import {
  anyTokenPattern,
  mixedRefPattern,
  parsePromptExpression,
  singleSlotRefPattern,
  slotRefPattern,
  tokenPattern,
} from './promptTokens';

describe('promptTokens', () => {
  it('槽位引用允许空格与修饰符，单张引用不认修饰符', () => {
    expect('{{ slot:hero }}'.match(slotRefPattern('hero'))).toBeTruthy();
    expect('{{slot:hero.2}}'.match(slotRefPattern('hero'))).toBeTruthy();
    expect('{{slot:hero}}'.match(singleSlotRefPattern('hero'))).toBeTruthy();
    expect('{{slot:hero*}}'.match(singleSlotRefPattern('hero'))).toBeNull();
  });

  it('Mixed 引用允许空格与修饰符', () => {
    expect('{{ Mixed 12 }}'.match(mixedRefPattern())).toBeTruthy();
    expect('{{Mixed 3*}}'.match(mixedRefPattern())).toBeTruthy();
  });

  it('tokenPattern 覆盖 slot / Mixed，anyTokenPattern 覆盖任意花括号', () => {
    expect('开头 {{slot:hero}} 结尾'.match(tokenPattern())).toHaveLength(1);
    expect('{{Mixed 2}}'.match(tokenPattern())).toHaveLength(1);
    expect('{{unknown}}'.match(tokenPattern())).toBeNull();
    expect('{{unknown}}'.match(anyTokenPattern())).toHaveLength(1);
  });

  it('parsePromptExpression 解析三种表达式，认不出返回 null', () => {
    expect(parsePromptExpression('slot:hero')).toEqual({
      kind: 'slot',
      key: 'hero',
      modifier: '',
      at: undefined,
    });
    expect(parsePromptExpression('slot:hero.2')).toMatchObject({
      kind: 'slot',
      key: 'hero',
      modifier: '.2',
      at: 2,
    });
    expect(parsePromptExpression('Mixed 3*')).toMatchObject({
      kind: 'mixed',
      index: 3,
      modifier: '*',
    });
    expect(parsePromptExpression('Text 4')).toEqual({ kind: 'text', index: 4 });
    expect(parsePromptExpression('nope')).toBeNull();
  });
});