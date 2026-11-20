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
