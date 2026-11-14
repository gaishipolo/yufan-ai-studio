/**
 * 资产库 v2 封装的成员用途（`library_pack_items.role`）。
 *
 * 资产库封装**没有** `kind` 字段（一个包里可能装着同一产品的图，也可能装着一个人
 * 的人像），所以用途标签只能是「与 kind 无关」的中性名：同一个 key 在产品包里叫
 * 「产品主图」、在人物包里叫「正面身份图」，拿某一 kind 的标签去渲染另一类包就会
 * 显示错名字。
 *
 * **服务端只把它当自由字符串**（`role` 上限 40 字，不校验词表），所以这份表是
 * 纯 UI 词汇：它决定下拉里能选什么、卡片上怎么显示，不决定任何判定。
 * 2026-11-14 之前它还有一份「必须与模板用途词表逐 key 对齐」的守卫 —— 那份词表
 * 随模板替换链下线了（`core/template_rules`），现在的约束只剩「不重不漏」。
 */

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

/** 全部用途 key（守卫用：与下拉选项同源，不另存一份）。 */
export function everyPackRoleKey(): string[] {
  return Object.keys(PACK_ITEM_ROLE_LABELS);
}

export function packItemRoleLabel(role: string | undefined): string {
  if (!role) return '未声明用途';
  return PACK_ITEM_ROLE_LABELS[role] ?? role;
}
