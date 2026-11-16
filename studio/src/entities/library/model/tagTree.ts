/**
 * 标签词表的多级视图（纯函数，不持有状态）。
 *
 * 后端 `GET /library/tags` 是**平铺数组**，每条带 `parentId` / `parentName` /
 * `depth` / `childCount`。标签树最多三级：一级是分组、二级是系列、三级是
 * 具体词。能打在素材上的只有**叶子**（`childCount === 0`），有下级的层级一律
 * 只做分组 —— 后端会拒掉把分组直接打在素材上的请求。
 *
 * 六个消费点（标签栏、打标选择器、卡片、上传弹窗、批量栏、素材库悬浮栏）都从
 * 这里派生自己要的那一份，不各写一遍 `filter(t => !t.parentId)` —— 那种重复正是
 * 「有的地方还把一级当可打标、有的地方已经拦了」的来源。
 *
 * 放在 `entities/library/model` 而不是 `features/library`：`entities/library/ui`
 * 的悬浮栏也要用，entities 不能反向依赖 features。
 */
import type { LibraryTagOut, LibraryTagStatOut } from '@/shared/api/schema';

type AnyTag = LibraryTagOut | LibraryTagStatOut;

/** 后端允许的最大层数：一级分组 → 二级系列 → 三级具体词。 */
export const MAX_TAG_DEPTH = 3;

export function isTopLevel(tag: AnyTag): boolean {
  return !tag.parentId;
}

/**
 * 能直接打在素材上的是**叶子**：有下级的层级只做分组。
 *
 * 刻意不写成「一级都不能打」：存量里「音乐」「AI生成」这些孤立一级
 * 没有子标签，拦掉它们会让几个正在用的词一夜之间变成看得见却选不了的死词。
 * 二级同理 —— 没有三级子标签的二级就是可打标的系列。
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

/**
 * 标签真实层数（1/2/3）。后端给了 `depth` 就用它；缺字段的旧数据
 * 沿 `parentId` 往上数，保证前端不因为少一个字段就把三级当二级。
 */
export function tagDepth(tags: LibraryTagStatOut[], tag: AnyTag): number {
  if (typeof tag.depth === 'number' && tag.depth >= 1) return tag.depth;
  const byId = new Map(tags.map((item) => [item.id, item]));
  let depth = 1;
  let parentId = tag.parentId ?? null;
  while (parentId && depth < MAX_TAG_DEPTH) {
    depth += 1;
    parentId = byId.get(parentId)?.parentId ?? null;
  }
  return depth;
}

/** 层级字面量（「一级」「二级」「三级」），按层数给，不写死在哪一层。 */
export function levelLabel(depth: number): string {
  if (depth === 1) return '一级';
  if (depth === 2) return '二级';
  if (depth === 3) return '三级';
  return `${depth} 级`;
}

export function topLevelTags(tags: LibraryTagStatOut[]): LibraryTagStatOut[] {
  return sortByCreated(tags.filter(isTopLevel));
}

/**
 * 传了 parentId 就取该父的**直属**子级（父可以是任意层级：传二级 id 就得到
 * 它下面的三级）；不传则返回全部二级（标签栏「未选一级」时的第二行）。
 */
export function childTags(
  tags: LibraryTagStatOut[],
  parentId?: string,
): LibraryTagStatOut[] {
  if (parentId) {
    return sortByCreated(tags.filter((tag) => tag.parentId === parentId));
  }
  return sortByCreated(
    tags.filter((tag) => !!tag.parentId && tagDepth(tags, tag) === 2),
  );
}

/** 某一层的标签（2 = 二级、3 = 三级）。 */
export function tagsAtDepth(
  tags: LibraryTagStatOut[],
  depth: number,
): LibraryTagStatOut[] {
  return sortByCreated(tags.filter((tag) => tagDepth(tags, tag) === depth));
}

/** 某标签及其**全部**后代的 id —— 选父清子时用。 */
export function descendantIds(
  tags: LibraryTagStatOut[],
  id: string,
): Set<string> {
  const childrenOf = new Map<string, string[]>();
  for (const tag of tags) {
    if (!tag.parentId) continue;
    childrenOf.set(tag.parentId, [...(childrenOf.get(tag.parentId) ?? []), tag.id]);
  }
  const result = new Set<string>();
  const stack = [id];
  while (stack.length) {
    const current = stack.pop() as string;
    for (const child of childrenOf.get(current) ?? []) {
      if (result.has(child)) continue;
      result.add(child);
      stack.push(child);
    }
  }
  return result;
}

/** 自身之外的祖先 id（从近到远）。选子级时要把同链的祖先移出选中。 */
export function ancestorIds(tags: LibraryTagStatOut[], tag: AnyTag): string[] {
  const byId = new Map(tags.map((item) => [item.id, item]));
  const result: string[] = [];
  let parentId = tag.parentId ?? null;
  while (parentId) {
    if (result.includes(parentId)) break;
    result.push(parentId);
    parentId = byId.get(parentId)?.parentId ?? null;
  }
  return result;
}

/** 往上找指定层级的祖先 id；自身已在该层时返回自身 id，找不到返回 null。 */
export function ancestorAtDepth(
  tags: LibraryTagStatOut[],
  tag: AnyTag,
  depth: number,
): string | null {
  const byId = new Map(tags.map((item) => [item.id, item]));
  let current: AnyTag | undefined = tag;
  let currentDepth = tagDepth(tags, tag);
  while (current && currentDepth > depth) {
    current = current.parentId ? byId.get(current.parentId) : undefined;
    currentDepth -= 1;
  }
  return current && currentDepth === depth ? current.id : null;
}

/** 打标选择器 / 上传 / 批量栏的选项：只给叶子（任意层级的叶子）。 */
export function selectableTags(tags: LibraryTagStatOut[]): LibraryTagStatOut[] {
  return sortByCreated(tags.filter(isSelectable));
}

/**
 * 显示名：「父链 / 名字」，例如「产品 / 3030 / T35」。
 *
 * 后端给的 `parentName` **已经是父的整条链**（三级标签的 parentName =「产品 / 3030」），
 * 所以这里只拼自己的名字；再把父名拼一遍会变成「产品 / 3030 / 3030 / T35」。
 */
export function tagLabel(tag: AnyTag): string {
  const parent = tag.parentName?.trim();
  return parent ? `${parent} / ${tag.name}` : tag.name;
}

/** 给 MultiSelect 之类的 `options` 用：带路径的显示名，值仍是 id。 */
export function tagOptions(tags: LibraryTagStatOut[]): { value: string; label: string }[] {
  return selectableTags(tags).map((tag) => ({ value: tag.id, label: tagLabel(tag) }));
}
