import type { Subject } from '@/shared/access/types';
import type { TokenOut } from '@/shared/api/schema';
import type { Me } from '@/entities/user/model/types';

/* ------------------------------------------------------------------
 * 会话实体（认证 / 本人资料）
 *
 * 与 `entities/user` 的分工：
 *   entities/user     —— 管理员视角的「别人」：列表、新建、停用、分配角色
 *   entities/session  —— 当事人视角的「自己」：登录、我是谁、改我的资料
 * 两者共用 `Me`（= User + permissions），只做单向类型依赖，没有运行时耦合。
 * ------------------------------------------------------------------ */

export interface LoginPayload {
  username: string;
  password: string;
}

/**
 * `POST /identity/login` / `POST /identity/refresh` 的令牌字段。
 *
 * **取自生成物 `schema.ts`**，不手抄：手抄过一次，结果后端在 `2fc0fc6` 给
 * `TokenOut` 加了 `refreshToken` / `refreshExpiresIn`，前端类型只写了 4 个字段，
 * 编译期没有任何提示，于是「服务端愿意让你待 7 天」这件事在前端被静默丢掉了。
 *
 * `tokenType` / `expiresIn` 在生成物里是可选的（后端有默认值），所以这里也是可选。
 */
export type TokenFields = Pick<
  TokenOut,
  'accessToken' | 'tokenType' | 'expiresIn' | 'refreshToken' | 'refreshExpiresIn'
>;

/** `POST /identity/login` 出参（后端 `TokenOut`）+ 手写的 `Me` */
export type TokenResult = TokenFields & {
  /** 登录用户，省一次 /me 请求 */
  user: Me;
};

/**
 * `PATCH /identity/me` 入参。
 * 刻意不含 isActive：停用是管理动作，自助路径只能改展示信息。
 */
export interface ProfilePatch {
  displayName?: string;
  email?: string | null;
  avatarUrl?: string | null;
}

/** `POST /identity/me/password` 入参（后端字段是 oldPassword / newPassword） */
export interface PasswordChangePayload {
  oldPassword: string;
  newPassword: string;
}

/**
 * 口令策略已下沉到 `entities/user/model/validation.ts`（与建号、重置共用一份）。
 * 这里不再二次导出：两条 import 路径指向同一个值，只会让「改哪一处」重新变成选择题。
 */

/**
 * 后端 Me → 骨架 Subject。
 * 骨架只认 id / name / roles / permissions，后端字段怎么变都只需改这一处。
 */
export function toSubject(me: Me): Subject {
  return {
    id: me.id,
    name: me.displayName || me.username,
    roles: me.roles.map((role) => role.code),
    permissions: me.permissions,
  };
}

/* 这里曾经还有一个 `displayNameOf(me)`，与 `entities/user/model/types` 里的同名
   函数语义不同（不 trim、不兜底），且零调用。删掉：昵称怎么取只能有一个答案，
   否则「顶栏显示空串、用户列表显示未命名」会同时成立。 */
