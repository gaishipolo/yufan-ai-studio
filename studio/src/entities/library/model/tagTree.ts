/**
 * 标签词表的两级视图（纯函数，不持有状态）。
 *
 * 后端 `GET /library/tags` 仍是**平铺数组**，只是每条多了 `parentId` /
 * `parentName` / `depth` / `childCount`。六个消费点（标签栏、打标选择器、
 * 卡片、上传弹窗、批量栏、素材库悬浮栏）都从这里派生自己要的那一份，
 * 不各写一遍 `filter(t => !t.parentId)` —— 那种重复正是「有的地方还把一级
 * 当可打标、有的地方已经拦了」的来源。
 *
 * 放在 `entities/library/model` 而不是 `features/library`：`entities/library/ui`
 * 的悬浮栏也要用，entities 不能反向依赖 features。
 */
import type { LibraryTagOut, LibraryTagStatOut } from '@/shared/api/schema';

type AnyTag = LibraryTagOut | LibraryTagStatOut;

export function isTopLevel(tag: AnyTag): boolean {
  return !tag.parentId;
}

/**
 * 能直接打在素材上的是**叶子**：有子标签的一级只做分组。
 *
 * 刻意不写成「一级都不能打」：存量里「音乐」「AI生成」「封装」这些孤立一级
 * 没有子标签，拦掉它们会让几个正在用的词一夜之间变成看得见却选不了的死词。
 */
export function isSelectable(tag: LibraryTagStatOut): boolean {
  return (tag.childCount ?? 0) === 0;
}

export function sortByCreated(tags: LibraryTagStatOut[]): LibraryTagStatOut[] {
  return [...tags].sort(
    (a, b) =>
      (a.createdAt ?? '').localeCompare(b.createdAt ?? '') || a.id.localeCompare(b.id),
  );
}

export function topLevelTags(tags: LibraryTagStatOut[]): LibraryTagStatOut[] {
  return sortByCreated(tags.filter(isTopLevel));
}

/** 传了 parentId 就只看该一级旗下；不传返回全部二级（标签栏「未选一级」时）。 */
export function childTags(
  tags: LibraryTagStatOut[],
  parentId?: string,
): LibraryTagStatOut[] {
  return sortByCreated(
    tags.filter((tag) => !!tag.parentId && (!parentId || tag.parentId === parentId)),
  );
}

/** 打标选择器 / 上传 / 批量栏的选项：只给叶子。 */
export function selectableTags(tags: LibraryTagStatOut[]): LibraryTagStatOut[] {
  return sortByCreated(tags.filter(isSelectable));
}

/** 卡片、下拉、标签栏第二行统一显示成「产品 / 3030」。 */
export function tagLabel(tag: AnyTag): string {
  return tag.parentName ? `${tag.parentName} / ${tag.name}` : tag.name;
}

/** 给 MultiSelect 之类的 `options` 用：带路径的显示名，值仍是 id。 */
export function tagOptions(tags: LibraryTagStatOut[]): { value: string; label: string }[] {
  return selectableTags(tags).map((tag) => ({ value: tag.id, label: tagLabel(tag) }));
}
