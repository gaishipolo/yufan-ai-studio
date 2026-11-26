import { describe, expect, it } from 'vitest';
import { expectedCapsuleCount } from './RichPromptEditor';

/**
 * 正文里该画成**胶囊**的 token 数量 —— 重绘判据的一半。
 *
 * 锚点段（默认描述）里的 token 是**纯文字**，不是胶囊：算进去两边永远不等，
 * 于是每次都重绘，鼠标拖选到一半 DOM 被重建、选区塌缩 → 框选菜单不出现 →
 * 「取消默认描述」点不到（用户报过的坑）。
 */
describe('正文里该画成胶囊的 token 数量', () => {
  it('普通正文里的 token 算进去', () => {
    expect(expectedCapsuleCount('主图 {{Mixed 1}}，场景 {{Mixed 2}}')).toBe(2);
  });

  it('默认描述段里的引用也算（那段里的引用同样画成胶囊）', () => {
    expect(expectedCapsuleCount('⟦e:image_1⟧见 {{Mixed 1}} 的构图⟦/e⟧')).toBe(1);
    expect(expectedCapsuleCount('前缀 {{Mixed 1}} ⟦e:image_1⟧见 {{Mixed 2}}⟦/e⟧ 后缀 {{Mixed 3}}')).toBe(3);
  });
});
