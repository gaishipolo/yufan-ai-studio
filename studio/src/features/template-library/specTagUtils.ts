import type { TagOut } from '@/shared/api/schema';

/**
 * 模板标签的纯函数。单独成文件是为了不打断 `SpecTagBar.tsx` 的热更新
 * （eslint `react-refresh/only-export-components`）——同目录的 `slotPick.ts`
 * 用的是同一套做法。
 */

/** 词表缺 `depth` 的旧数据沿 `parentId` 推：两级模型里有父就是取值。 */
export function templateTagDepth(tag: TagOut): number {
  return typeof tag.depth === 'number' && tag.depth >= 1
    ? tag.depth
    : tag.parentId
      ? 2
      : 1;
}

/** 胶囊上写短名，悬停给全路径（与素材库标签栏一致：短名好看，路径说清挂在哪条轴）。 */
export function templateTagLabel(tag: TagOut): string {
  const parent = tag.parentName?.trim();
  return parent ? `${parent} / ${tag.name}` : tag.name;
}

/**
 * 真正发后端的标签：**同维度里选了维度又选了它的取值 → 只留取值**。
 *
 * 后端在维度组内是 OR，把父和子一起送上去等于「整个维度」—— 点了「6036」
 * 却仍看到同维度的其它取值。收敛只发生在这里，界面上的胶囊全部保留高亮
 * （与素材库标签栏同一条约定）。
 */
export function effectiveTemplateTagIds(tags: TagOut[], selected: string[]): string[] {
  const parentOf = new Map(tags.map((tag) => [tag.id, tag.parentId ?? null]));
  const chosen = [...new Set(selected)].filter((id) => parentOf.has(id));
  return chosen.filter((id) =>
    !chosen.some((other) => {
      if (other === id) return false;
      let cursor = parentOf.get(other) ?? null;
      while (cursor) {
        if (cursor === id) return true;
        cursor = parentOf.get(cursor) ?? null;
      }
      return false;
    }),
  );
}
