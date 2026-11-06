import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

/* ------------------------------------------------------------------
 * 这份 CSS 是全局引入的（不是 CSS Modules），命名空间只能靠规则守住。
 * 曾经有一条 `.is-selected .replacement-card-check`：`.is-selected` 是通用
 * 状态类，会匹配任何组件里的同名元素。改成由 `.replacement-template-card`
 * 限定后，这里钉住「顶层选择器必须带前缀」，防止再写回去。
 * ------------------------------------------------------------------ */

const CSS_PATH = join(
  process.cwd(),
  'src/features/replacement-templates/replacement-templates.css',
);

describe('replacement-templates 样式隔离', () => {
  it('每个顶层类选择器都由 replacement- 前缀限定', () => {
    const source = readFileSync(CSS_PATH, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
    const selectors = source
      .split('\n')
      .map((line) => line.trim())
      // 只取类选择器行；@media / @keyframes / from / to / 百分比关键帧不在其列
      .filter((line) => line.startsWith('.') && line.endsWith('{'));

    expect(selectors.length).toBeGreaterThan(20);
    for (const selector of selectors) {
      expect(
        selector.startsWith('.replacement-'),
        `选择器「${selector}」没有 replacement- 前缀，会溢出到其它组件`,
      ).toBe(true);
    }
  });
});
