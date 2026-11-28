/**
 * 两级标签的选择语义：**高亮什么、第二行列什么、请求发什么**。
 *
 * 素材库、模板库、模板封装三处都用同一套：
 * - 选中二级时父维度保持高亮（用户要看见自己选了哪条轴）；
 * - 第二行按选中的维度收窄；
 * - 请求只发**最具体**的那一档（父的范围包含子，两个都发等于整个维度）。
 *
 * `single` 只是交互约束（每层最多一个）；多选时同一维度的多个取值仍然都发，
 * 后端组内是 OR。
 */
export interface TagLike {
  id: string;
  parentId?: string | null;
}

export interface TagSelection<T extends TagLike> {
  activeIds: Set<string>;
  dimensions: T[];
  values: T[];
  requestIds: string[];
}

function parentMap(tags: TagLike[]): Map<string, string | null> {
  return new Map(tags.map(tag => [tag.id, tag.parentId ?? null]));
}

function ancestorsOf(parentOf: Map<string, string | null>, id: string): string[] {
  const result: string[] = [];
  let parent = parentOf.get(id) ?? null;
  while (parent) {
    if (result.includes(parent)) break;
    result.push(parent);
    parent = parentOf.get(parent) ?? null;
  }
  return result;
}

/** 选中的标签 → 展示用与请求用的视图。 */
export function tagSelection<T extends TagLike>(
  tags: T[],
  selected: string[],
): TagSelection<T> {
  const parentOf = parentMap(tags);
  const known = new Set(tags.map(tag => tag.id));
  const chosen = [...new Set(selected)].filter(id => known.has(id));

  const activeIds = new Set(chosen);
  for (const id of chosen) {
    for (const ancestor of ancestorsOf(parentOf, id)) activeIds.add(ancestor);
  }

  const dimensions = tags.filter(tag => !tag.parentId);
  const selectedDimensions = new Set(
    dimensions.filter(tag => activeIds.has(tag.id)).map(tag => tag.id),
  );
  const allValues = tags.filter(tag => !!tag.parentId);
  const values = selectedDimensions.size
    ? allValues.filter(tag => tag.parentId && selectedDimensions.has(tag.parentId))
    : allValues;

  const requestIds = chosen.filter(
    id =>
      !chosen.some(other => {
        if (other === id) return false;
        return ancestorsOf(parentOf, other).includes(id);
      }),
  );

  return {activeIds, dimensions, values, requestIds};
}

/** 点一个标签后的完整选中数组。 */
export function toggleTagSelection<T extends TagLike>(
  tags: T[],
  selected: string[],
  tag: T,
  {single = true}: {single?: boolean} = {},
): string[] {
  const selection = tagSelection(tags, selected);
  const parentOf = parentMap(tags);
  const chosen = [...new Set(selected)];

  if (!tag.parentId) {
    if (selection.activeIds.has(tag.id)) {
      return chosen.filter(id => {
        if (id === tag.id) return false;
        return !ancestorsOf(parentOf, id).includes(tag.id);
      });
    }
    return single ? [tag.id] : [...chosen, tag.id];
  }

  if (selection.activeIds.has(tag.id)) {
    return chosen.filter(id => id !== tag.id);
  }
  return single ? [tag.parentId, tag.id] : [...chosen, tag.id];
}

/** 只要请求 id 的便捷入口。 */
export function tagRequestIds(tags: TagLike[], selected: string[]): string[] {
  return tagSelection(tags, selected).requestIds;
}
