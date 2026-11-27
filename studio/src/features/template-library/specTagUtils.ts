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
 * 真正发后端的标签：**同一条链上只留二级取值**。
 *
 * 界面上「维度 + 它下面的取值」可以同时高亮（用户要看到自己选了哪条轴），
 * 但请求不能两个都发：父的范围包含子，一起发等于整个维度，点「6036」
 * 会变回看整个产品线。跨维度也不会发生（每层最多一个）。
 */
export function effectiveTemplateTagIds(tags: TagOut[], selected: string[]): string[] {
  const parentOf = new Map(tags.map((tag) => [tag.id, tag.parentId ?? null]));
  const chosen = [...new Set(selected)].filter((id) => parentOf.has(id));
  return chosen.filter((id) => !chosen.some((other) => parentOf.get(other) === id));
}
