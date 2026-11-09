/**
 * 三层授权（团队 / 小组 / 个人）的实体类型。
 *
 * 与 `entities/user` 同一条纪律：**从生成物派生，不手抄**。
 * `GroupOut` / `EffectivePermissionsOut` 由 `app export-openapi` 生成，
 * 后端字段一改，这里立刻在编译期暴露，而不是运行时出现 undefined。
 */
import type { EffectivePermissionsOut, GroupOut, UserOut } from '@/shared/api/schema';

export type Group = GroupOut;
export type EffectivePermissions = EffectivePermissionsOut;
export type GroupMember = UserOut;

export interface GroupCreatePayload {
  name: string;
  description?: string;
}

export interface GroupUpdatePayload {
  name?: string;
  description?: string;
}
