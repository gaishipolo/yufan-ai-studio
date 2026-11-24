import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * 卡片封面层必须显式带 w-full。
 *
 * 那台老 Edge 上 flex column 没对卡片内第一层执行 stretch，宽度算成 0；
 * 封面是 absolute + aspect-ratio，撑不开父级，于是整棵封面子树 0×0 ——
 * 图片其实已解码成功（naturalWidth 720），却没有地方画。
 * jsdom 不做真实布局，测不出宽度塌陷，所以用源码扫描钉住这个约束。
 */
describe('模板卡片封面宽度', () => {
  const source = readFileSync(resolve(__dirname, 'SpecLibrary.tsx'), 'utf-8');

  it('封面包裹层显式 w-full（不依赖 flex stretch）', () => {
    const wrapper = source.match(/<div className="([^"]*rounded-xl[^"]*)">/);
    expect(wrapper, '找不到封面包裹层').toBeTruthy();
    expect(wrapper?.[1]).toContain('w-full');
  });

  it('按钮显式 items-stretch，双保险', () => {
    expect(source).toContain('flex flex-col items-stretch');
  });
});
