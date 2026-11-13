/**
 * 资产库 v2 封装的成员用途（`library_pack_items.role`）。
 *
 * 为什么需要一份「与 kind 无关」的标签：v2 封装**没有** `kind` 字段（一个包里可能
 * 装着同一产品的图，也可能装着一个人的人像），而用途词表是按 kind 分的
 * （`entities/template/model/packRoles.ts`）。同一个 key 在产品包叫「产品主图」、
 * 在人物包叫「正面身份图」，直接拿某一 kind 的标签渲染会让另一类包显示错名字。
 *
 * 这里只做两件事，都不改变服务端的判定：
 * 1. 列出**当前模板替换认得**的用途 key（词表来源仍是 `PACK_ROLES`，不新造 key）；
 * 2. 给每个 key 一个跟 kind 无关的中性名，只用于封装页的下拉。
 *
 * 用途真正被解释时（`{{Mixed N}}` 取位、提示词说明文字）仍由调用方的 `kind`
 * 决定，所以这里**不**做「这个包是什么类型」的推断。
 */
import { PACK_ROLES } from '@/entities/template/model/packRoles';

/** key → 中性名。顺序即封装页下拉的顺序。 */
export const PACK_ITEM_ROLE_LABELS: Record<string, string> = {
  front: '主图（正面）',
  multiview: '多视图',
  side: '侧面图',
  back: '背面图',
  detail: '细节图',
  fullbody: '全身图',
  main: '主参考图',
  angle: '补充视角',
};

/** 可用用途（保持 `PACK_ITEM_ROLE_LABELS` 的声明顺序）。 */
export const PACK_ITEM_ROLES: string[] = Object.keys(PACK_ITEM_ROLE_LABELS);

/** 词表里所有 kind 出现过的用途 key。用于守卫「中性名不能漏 key」。 */
export function everyPackRoleKey(): string[] {
  const keys = new Set<string>();
  for (const roles of Object.values(PACK_ROLES)) {
    for (const key of Object.keys(roles)) keys.add(key);
  }
  return [...keys];
}

export function packItemRoleLabel(role: string | undefined): string {
  if (!role) return '未声明用途';
  return PACK_ITEM_ROLE_LABELS[role] ?? role;
}
