/**
 * 把一列槽位切成「独立槽位 / 封装容器」的画面单元。
 *
 * 容器不是一条独立记录：它就是槽位上的一列 `group`，同一 key 的槽位在顺序上
 * 必须相邻（后端 `rules._validate_groups` 会拦）。所以这里只按**相邻**归并，
 * 不做「按 key 全局搜集」—— 那会把一个断开的结构悄悄拼回去，而它在别处
 * 已经被拒绝过一次了。
 *
 * 封装页与模板库都要按这个切分来画（作者端画成框，使用端画成一个选包入口），
 * 两处用同一份实现，省得「作者看到的容器」与「使用者看到的容器」不是同一组。
 */
export interface SlotGroupMember<T> {
  slot: T;
  /** 在原始数组里的下标（删除、定位都按它）。 */
  index: number;
}

export interface SlotGroupRun<T> {
  /** 空串 = 独立槽位（`members` 恒为一项）。 */
  group: string;
  members: SlotGroupMember<T>[];
}

export function groupRuns<T extends {key: string; group?: string}>(slots: T[]): SlotGroupRun<T>[] {
  const runs: SlotGroupRun<T>[] = [];
  slots.forEach((slot, index) => {
    const group = (slot.group ?? '').trim();
    const last = runs[runs.length - 1];
    if (group && last && last.group === group) {
      last.members.push({slot, index});
      return;
    }
    runs.push({group, members: [{slot, index}]});
  });
  return runs;
}

/** 这个槽位在不在封装容器里。 */
export function inPackGroup(slot: {group?: string}): boolean {
  return Boolean((slot.group ?? '').trim());
}

/** 容器名的契约形状（`SpecGroupIn` 的最小面）。 */
export interface SpecGroupLike {
  key: string;
  label?: string;
}

/** 没起名的容器按段序回退成这个（用户定的默认名，会随草稿落库）。 */
export function defaultGroupLabel(index: number): string {
  return `封装槽位${index}`;
}

/**
 * 每个容器叫什么：声明了的用声明的，没声明的按段序回退「封装槽位1/2/…」。
 *
 * **为什么要有回退**：`groups` 是后加的字段，老模板的草稿与老发布版本里都没有，
 * 拿不到名字就显示空 —— 那一块会变成只有一个「2/2」计数的无名框。回退让老模板
 * 一打开就有个能读的名字，作者想改再改。
 *
 * 编号按**段序**而不是 key：key 是自动生成的（`g1`、`g2`），删了中间一个容器后
 * 剩下的 key 是 `g1`、`g3`，按 key 编号会出现「封装槽位1、封装槽位3」。
 *
 * 同一个容器只记一次（`groupRuns` 保证同一 key 只在相邻段里，但作者可能手写出
 * 两段同 key 的坏数据；那时模板库只画第一段，名字也跟着第一段）。
 */
export function groupLabels<T extends {key: string; group?: string}>(
  slots: T[],
  groups?: SpecGroupLike[] | null,
): Map<string, string> {
  const declared = new Map<string, string>();
  for (const group of groups ?? []) {
    const key = (group.key ?? '').trim();
    if (!key) continue;
    const label = (group.label ?? '').trim();
    // 声明了但没起名（作者清空了输入框）→ 也走回退，否则界面上是个空标题。
    if (label) declared.set(key, label);
  }
  const labels = new Map<string, string>();
  let index = 0;
  for (const run of groupRuns(slots)) {
    if (!run.group || labels.has(run.group)) continue;
    index += 1;
    labels.set(run.group, declared.get(run.group) ?? defaultGroupLabel(index));
  }
  return labels;
}
