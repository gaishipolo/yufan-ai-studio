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

/**
 * 选中的标签 → 真正拿去筛选的标签 id。
 *
 * 三级标签现在可以**任意组合**：跨链多选是并集（选「产品」与选「角色」并存，
 * 结果是两边的范围叠起来），但**同一条链上只按最具体的那一层筛选** ——
 * 父的范围本来就含子，两个都留着只会让筛选范围重复：用户点了「产品 / 3030」，
 * 想要的是 3030 那一档，不是整个产品线。
 *
 * 收敛的判据只有一个：**有更具体的一档被选中了**。同一条链上父与子只留最具体的那一层
 * （父的范围含子，两个都留着是重复）；而兄弟分支互不包含，两支都要留着。
 * （早期版本按「链根」分组，同一根下的兄弟分支会被一起吞掉：勾了「产品 /
 * T40」与「产品 / 3030 / X100」，其中一支被静默筛掉，胶囊却还在高亮。）
 *
 * 后端那边一律是 OR（展开成「自身 + 全部下级」），这条收敛只发生在界面层：
 * 它决定「我们想看哪一档」，而不是后端怎么算。
 */
export function effectiveTagSelection(
  tags: LibraryTagStatOut[],
  selected: string[],
): string[] {
  const byId = new Map(tags.map((item) => [item.id, item]));
  const chosen = [...new Set(selected)].filter((id) => byId.has(id));
  const result: string[] = [];
  for (const id of chosen) {
    const tag = byId.get(id);
    if (!tag) continue;
    /* 有更具体的一档被选中了就让位：同一条链上父与子只留最具体的那一层。
       兄弟分支互不包含 —— 「产品 / T40」与「产品 / 3030 / X100」两支都要留着，
       否则其中一整支素材会被静默筛掉，而胶囊还在高亮。 */
    const covered = chosen.some((other) => {
      if (other === id) return false;
      const otherTag = byId.get(other);
      return otherTag ? ancestorIds(tags, otherTag).includes(id) : false;
    });
    if (!covered) result.push(id);
  }
  return result;
}


/**
 * 候选集范围（scope）允许挑的标签：scope 本身 + 它的全部后代。
 *
 * 为什么只给后代：槽位的标签是作者定的**候选集**，模板使用者只能在它里面挑。
 * 圈到范围外面去，作者与使用者看到的就是两批素材 —— 「作者选的图用户根本挑不到」。
 *
 * scope 为空 = 不限，这时所有标签都能挑。
 */
export function scopeAllowedTags(
  tags: LibraryTagStatOut[],
  scope: string[],
): LibraryTagStatOut[] {
  if (!scope.length) return tags;
  const allowed = new Set<string>();
  for (const id of scope) {
    allowed.add(id);
    for (const child of descendantIds(tags, id)) allowed.add(child);
  }
  return tags.filter((tag) => allowed.has(tag.id));
}

/**
 * 槽位候选集用的标签选项：**任意级别**（分组也能圈），显示名带整条链。
 *
 * 槽位的标签是「圈定候选范围」，不是「打在素材上」，所以不必限叶子 ——
 * 圈一个分组正是它的用途。打标选择器要的才是叶子，那边用 `tagOptions`。
 */
export function scopeTagOptions(
  tags: LibraryTagStatOut[],
): { value: string; label: string }[] {
  return sortByCreated(tags).map((tag) => ({ value: tag.id, label: tagLabel(tag) }));
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
