/**
 * 标签词表的多级视图（纯函数，不持有状态）。
 *
 * 后端 `GET /library/tags` 是**平铺数组**，每条带 `parentId` / `parentName` /
 * `depth` / `childCount`。标签树最多**两级**：一级是**维度**（产品 / 画面类型 /
 * 角色），二级是**取值**（6036 / 正面图 / 菜菜）。能打在素材上的只有**叶子**
 * （`childCount === 0`），有下级的层级一律只做分组 —— 后端会拒掉把分组直接
 * 打在素材上的请求。
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

/** 后端允许的最大层数：一级维度 → 二级取值。 */
export const MAX_TAG_DEPTH = 2;

export function isTopLevel(tag: AnyTag): boolean {
  return !tag.parentId;
}

/**
 * 能直接打在素材上的是**叶子**：有下级的层级只做分组。
 *
 * 刻意不写成「一级都不能打」：存量里「音乐」「AI生成」这些孤立一级
 * 没有子标签，拦掉它们会让几个正在用的词一夜之间变成看得见却选不了的死词。
 * 同理，删掉一个维度后它的取值会被提升为一级 —— 那些取值仍然要能打。
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
 * 标签真实层数（1/2）。后端给了 `depth` 就用它；缺字段的旧数据
 * 沿 `parentId` 往上数，保证前端不因为少一个字段就把层级算错。
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

/** 层级字面量（「一级」「二级」），按层数给，不写死在哪一层。 */
export function levelLabel(depth: number): string {
  if (depth === 1) return '一级';
  if (depth === 2) return '二级';
  return `${depth} 级`;
}

export function topLevelTags(tags: LibraryTagStatOut[]): LibraryTagStatOut[] {
  return sortByCreated(tags.filter(isTopLevel));
}

/**
 * 传了 parentId 就取该父的**直属**子级（取值）；不传则返回全部取值
 * （标签栏「未选维度」时的第二行）。
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

/** 某一层的标签（1 = 维度、2 = 取值）。 */
export function tagsAtDepth(
  tags: LibraryTagStatOut[],
  depth: number,
): LibraryTagStatOut[] {
  return sortByCreated(tags.filter((tag) => tagDepth(tags, tag) === depth));
}

/**
 * 某个标签所在的**维度** id —— 它自己是一级时就是自己，否则是它的一级祖先。
 *
 * 两级模型里「一级祖先」就是唯一的维度：二级取值只有一个父。
 */
export function dimensionOf(
  tags: LibraryTagStatOut[],
  tag: AnyTag,
): string | null {
  const depth = tagDepth(tags, tag);
  if (depth <= 1) return tag.id;
  const ancestor = ancestorAtDepth(tags, tag, 1);
  return ancestor ?? tag.id;
}

/**
 * 二级行该列哪些取值：**只列当前选中项涉及的维度下的取值**。
 *
 * 这是用户 2026-11-17 提的第一条：点了「产品」，二级行就该只剩产品的取值；
 * 点了「产品」+「角色」，二级行就该是这两条轴的取值之和。
 *
 * 规则（两级模型下唯一自洽的一套）：
 *
 * - 选中里有**一级维度** → 列该维度下的全部取值；
 * - 选中里有**二级取值** → 列它所属维度的**全部**取值（用户能顺手续选
 *   同一维度的兄弟取值，那才是「同维度多选是或」）；
 * - 选中里没有任何标签 → 列**全部**取值（没圈范围时词表几十条，搜得到就行）。
 *
 * 只按「选中项涉及哪些维度」过滤，不按「选中项自身」过滤：用户点了
 * 「产品 / 3030」之后还得能看见「产品 / T40」，否则同维度多选根本没法做。
 *
 * 返回的是**该维度下的全部取值**（不止选中的那个），所以这是「收窄候选」
 * 而不是「按选择筛结果」。
 */
export function secondLevelForSelection(
  tags: LibraryTagStatOut[],
  selected: string[],
): LibraryTagStatOut[] {
  const byId = new Map(tags.map((item) => [item.id, item]));
  const chosen = [...new Set(selected)].filter((id) => byId.has(id));
  if (!chosen.length) return tagsAtDepth(tags, 2);

  /* 选中项涉及的维度：每个选中项归到它自己那个维度 */
  const dimensions = new Set<string>();
  for (const id of chosen) {
    const tag = byId.get(id);
    if (!tag) continue;
    const dimension = dimensionOf(tags, tag);
    if (dimension) dimensions.add(dimension);
  }
  if (!dimensions.size) return tagsAtDepth(tags, 2);

  return tagsAtDepth(tags, 2).filter((tag) => {
    const dimension = dimensionOf(tags, tag);
    return dimension ? dimensions.has(dimension) : false;
  });
}

/**
 * 取值行该列哪些标签：**能打在素材上的叶子**，按选中项涉及的维度收窄。
 *
 * 与 `secondLevelForSelection` 的区别是「按叶子取」而不是「按层级取」：
 * 两级模型下叶子基本就是二级取值，但**孤立的一级**（「音乐」这种没子标签的词）
 * 也是叶子、也真的能打在素材上 —— 按层级取会把它们漏掉，按叶子取就不会。
 *
 * 反过来，有子标签的一级（产品 / 产品图类型）**不是叶子**，自然不出现 ——
 * 它们只是分组头，不是能挑的取值。
 */
export function leafTagsForSelection(
  tags: LibraryTagStatOut[],
  selected: string[],
): LibraryTagStatOut[] {
  const byId = new Map(tags.map((item) => [item.id, item]));
  const leaves = sortByCreated(tags.filter(isSelectable));
  const chosen = [...new Set(selected)].filter((id) => byId.has(id));
  if (!chosen.length) return leaves;

  /* 选中项涉及的维度：每个选中项归到它自己那个维度 */
  const dimensions = new Set<string>();
  for (const id of chosen) {
    const tag = byId.get(id);
    if (!tag) continue;
    const dimension = dimensionOf(tags, tag);
    if (dimension) dimensions.add(dimension);
  }
  if (!dimensions.size) return leaves;

  return leaves.filter((tag) => {
    const dimension = dimensionOf(tags, tag);
    return dimension ? dimensions.has(dimension) : false;
  });
}

/**
 * 某标签及其**全部**后代的 id —— 选父清子时用。 */
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
 * 两级模型下规则是「**同维度只留最具体的那一层**」：
 *
 * - 同维度多选（6036 与 T35）**都留着** —— 用户想同时看两个产品，
 *   后端在同一维度内按 OR 处理。
 * - 跨维度多选（正面图 与 6036）**都留着** —— 后端在不同维度间按 AND 处理，
 *   「既是 6036 又是正面图」。
 * - 同维度里选了维度又选了它下面的取值（产品 与 6036）→ **只留取值**：
 *   维度的范围本来就含它的取值，两个都留着等于「整个产品线」，
 *   而用户点「6036」要的是 6036 那一档。
 *
 * 判据只有一个：**有更具体的一档被选中了**（更具体 = 更深一层）。
 * 两级模型下「同一条链」就是「同一个维度」，所以这条规则同时管住了
 * 维度与取值的关系、以及取值之间的关系。
 *
 * 后端那边是「组内 OR、组间 AND」（见 `tags.tag_filter_clause`），
 * 这条收敛只发生在界面层：它决定「我们想看哪一档」。
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
    /* 有更具体的一档被选中了就让位：同一维度里维度与取值只留取值。
       兄弟取值互不包含 —— 「6036」与「T35」两支都要留着，
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
 * 真正拿去筛素材的标签：**候选集 + 使用者的挑选**，按维度做「替换」。
 *
 * 这是模板库的核心口径 —— 作者圈了候选集（那是**素材范围**），
 * 使用者在界面上的点击是**在这个范围里再筛**。所以同一维度上，
 * 使用者挑的那一档**替换**掉候选集里这一维度的标签，而不是叠加：
 *
 * - 候选集 =「产品 + 三视图」，使用者挑「产品 / 6036」
 *   → 结果 =「6036 + 三视图」（既是 6036 又是三视图）。
 *   叠加会变成三个维度、要求素材同时挂三样 —— 那不是「再筛」，是「再限」。
 * - 候选集 =「产品」，使用者挑「角色 / 菜菜」
 *   → 结果 =「产品 + 菜菜」：菜菜那条轴候选集没管过，由使用者说了算。
 * - 候选集 = 空，使用者挑「产品 / 6036」→ 就是「6036」。
 *
 * 与后端一致：跨维度是 AND，同维度是 OR（`tags.tag_filter_clause`）。
 */
export function scopeFilterIds(
  tags: LibraryTagStatOut[],
  scope: string[],
  picked: string[],
): string[] {
  const byId = new Map(tags.map((item) => [item.id, item]));
  /* 维度 → 该维度要用的标签 id。先填候选集，再用使用者的挑选逐维度替换。 */
  const byDimension = new Map<string, string[]>();
  for (const id of scope) {
    const tag = byId.get(id);
    if (!tag) continue;
    const dimension = dimensionOf(tags, tag);
    if (!dimension) continue;
    byDimension.set(dimension, [...(byDimension.get(dimension) ?? []), id]);
  }
  for (const id of picked) {
    const tag = byId.get(id);
    if (!tag) continue;
    const dimension = dimensionOf(tags, tag);
    if (!dimension) continue;
    /* 这一维度由使用者说了算：候选集在这一维度的标签让位。 */
    byDimension.set(dimension, [id]);
  }

  const result: string[] = [];
  for (const ids of byDimension.values()) {
    for (const id of ids) {
      if (!result.includes(id)) result.push(id);
    }
  }
  return result;
}

/**
 * 槽位候选集用的标签选项：**任意级别**（维度也能圈），显示名带整条链。
 *
 * 槽位的标签是「圈定候选范围」，不是「打在素材上」，所以不必限叶子 ——
 * 圈一个维度正是它的用途。打标选择器要的才是叶子，那边用 `tagOptions`。
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
 * 显示名：「父链 / 名字」，例如「产品 / 6036」。
 *
 * 后端给的 `parentName` **已经是父的整条链**（取值的 parentName =「产品」），
 * 所以这里只拼自己的名字；再把父名拼一遍会变成「产品 / 产品 / 6036」。
 */
export function tagLabel(tag: AnyTag): string {
  const parent = tag.parentName?.trim();
  return parent ? `${parent} / ${tag.name}` : tag.name;
}

/** 给 MultiSelect 之类的 `options` 用：带路径的显示名，值仍是 id。 */
export function tagOptions(tags: LibraryTagStatOut[]): { value: string; label: string }[] {
  return selectableTags(tags).map((tag) => ({ value: tag.id, label: tagLabel(tag) }));
}
