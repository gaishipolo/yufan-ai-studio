import { describe, expect, it } from 'vitest';
import { stripSlotRefsAuthored } from './specDraft';

/* 删掉槽位后，正文里留下的引用要仍是**作者写法**（`{{Mixed N}}`）。
   直接用 stripSlotRefs 会把内部写法（`{{slot:hero}}`）漏进正文 ——
   作者看到的是一串看不懂的花括号，胶囊上也认不出槽位名。

   调用方（SpecWorkspace.removeSlots）会先 renumberRefs（把正文换成内部写法
   才能按 key 删），所以这里收的是内部写法、吐的是作者写法。 */
describe('删槽位后正文的写法', () => {
  it('删掉被引用的槽位后，其余引用仍是 {{Mixed N}}', () => {
    const remaining = [
      { key: 'hero', type: 'image' as const, refMode: 'mixed' as const, minCount: 1, maxCount: 1, position: 0 },
    ];
    const result = stripSlotRefsAuthored(
      '主图 {{slot:hero}}，场景 {{slot:scene}}',
      'scene',
      remaining,
    );
    expect(result.count).toBe(1);
    expect(result.text).toBe('主图 {{Mixed 1}}，场景 ');
    expect(result.text).not.toContain('{{slot:');
  });
});
