import type { ChipTone } from '@/shared/ui';

/**
 * 主体卡片上的三种状态。
 *
 * 三态而不是布尔，因为用户下一步的动作完全不同：
 *
 * - `ready`：能发布、能被模板调用。什么也不用做
 * - `broken`：成员图被删了。下一步是**去回收站恢复**那张图
 * - `incomplete`：缺角色或缺能力。下一步是**改配置**（补一张图 / 抬授权）
 *
 * 压成"能不能用"的话，两件事会共用一句"不可用"，而用户看着那句话不知道该
 * 去找一张图还是去翻权限页。
 */
export const AVAILABILITY: Record<string, {label: string; tone: ChipTone; hint: string}> = {
  ready: {label: '可发布', tone: 'ok', hint: '成员齐、能力够，可以被模板调用'},
  broken: {label: '缺图', tone: 'danger', hint: '有成员素材已被删除，先去回收站恢复'},
  incomplete: {label: '待补齐', tone: 'warn', hint: '还缺角色或能力，点进详情看是哪一张'},
};

/**
 * 未知状态一律按 `incomplete` 渲染，不抛错。
 *
 * 后端将来加第四态时，前端最差的后果是"显示成待补齐"，而不是整张卡片炸掉——
 * 少一个角标可以补，白屏不能。
 */
export function availabilityOf(state?: string) {
  return AVAILABILITY[state ?? ''] ?? AVAILABILITY.incomplete;
}

/** 成员条带上每张图允许的能力。中文标签只在这里出现一次，别在组件里再写一遍。 */
export const GRANT_LABEL: Record<string, string> = {
  generate: '可生成',
  download: '可下载',
  derive: '可衍生',
};

export function grantLabel(code: string) {
  return GRANT_LABEL[code] ?? code;
}

/** 卡片上那行"3 张成员 · v2"：没有成员时不该显示成"0 张成员"。 */
export function memberSummary(count?: number, version?: number) {
  const members = count ? `${count} 张成员` : '还没有成员';
  return version ? `${members} · v${version}` : members;
}
