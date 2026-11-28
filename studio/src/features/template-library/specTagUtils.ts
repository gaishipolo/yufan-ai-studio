import type { TagOut } from '@/shared/api/schema';

/**
 * 模板标签的纯函数。单独成文件是为了不打断 `SpecTagBar.tsx` 的热更新
 * （eslint `react-refresh/only-export-components`）——同目录的 `slotPick.ts`
 * 用的是同一套做法。
 */

/** 胶囊上写短名，悬停给全路径（与素材库标签栏一致：短名好看，路径说清挂在哪条轴）。 */
export function templateTagLabel(tag: TagOut): string {
  const parent = tag.parentName?.trim();
  return parent ? `${parent} / ${tag.name}` : tag.name;
}
