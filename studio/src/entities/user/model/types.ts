/**
 * 用户实体类型 —— 从生成物 `shared/api/schema.ts` 派生，**不要手抄**。
 *
 * 手抄过一次的代价：后端在 `2fc0fc6` 给 `TokenOut` 加了 `refreshToken`，
 * 前端只写了 4 个字段，编译期毫无提示；同一个坑换到 `UserOut` 上，
 * 后果更重 —— `can()`、列表筛选、`isSuperuser()` 全读 `isActive` / `roles`。
 *
 * 生成物里 `displayName` / `isActive` / `roles` 是**可选**的，因为 Pydantic 给它们
 * 写了默认值（`exclude_unset` 语义）。但契约上这些字段一定发，所以这里显式收窄：
 * 收窄写进类型，等于把「一定发」变成编译期断言 —— 后端哪天真不发了，
 * 读取处会直接报错，而不是在运行时变成 `undefined` 让权限判定失准。
 */
import type { MeOut, RoleBriefOut, UserOut } from '@/shared/api/schema';

/** 把 `K` 里的字段从可选改成必填，其余原样保留 */
type RequiredFields<T, K extends keyof T> = Omit<T, K> & Required<Pick<T, K>>;

/** 用户出参里内嵌的角色摘要（`RoleBriefOut`） */
export type RoleBrief = RequiredFields<RoleBriefOut, 'isSuperuser'>;

export type User = RequiredFields<UserOut, 'displayName' | 'isActive' | 'roles'>;

/** `GET /identity/me` 比 `UserOut` 多一组展开后的权限码（`MeOut`） */
export type Me = RequiredFields<
  MeOut,
  'displayName' | 'isActive' | 'roles' | 'permissions'
>;

export interface UserListParams {
  page?: number;
  pageSize?: number;
  /** 匹配用户名 / 昵称 / 邮箱 / 手机号 / 职务 */
  keyword?: string;
  /** 按姓名（昵称）模糊筛选；截图顶栏的第一个搜索框 */
  name?: string;
  /** 按账号模糊筛选 */
  username?: string;
  /** 按手机号模糊筛选 */
  phone?: string;
  isActive?: boolean;
  roleId?: string;
  /** 只看该小组的成员；不传 = 全部分组 */
  groupId?: string;
}

export interface UserListResult {
  items: User[];
  total: number;
  page: number;
  pageSize: number;
}

/** `POST /identity/users` 入参 */
export interface CreateUserPayload {
  username: string;
  /** 至少 10 位、不能纯数字（后端策略） */
  password: string;
  displayName?: string;
  email?: string | null;
  /** 仅展示，不参与业务校验（Q1 待确认，当前按可选字符串） */
  phone?: string | null;
  /** 仅展示 */
  position?: string | null;
  roleIds?: string[];
}

/** `PATCH /identity/users/{id}` 入参：只传要改的字段 */
export interface UpdateUserPayload {
  displayName?: string;
  email?: string | null;
  avatarUrl?: string | null;
  phone?: string | null;
  position?: string | null;
  isActive?: boolean;
}

/** 展示名兜底：后端允许 displayName 为空串 */
export function displayNameOf(user: { displayName?: string | null; username?: string }): string {
  return user.displayName?.trim() || user.username || '未命名';
}

/** 头像占位：取昵称首字符（中文取第一个字，英文取首字母大写） */
export function initialsOf(user: { displayName?: string | null; username?: string }): string {
  return displayNameOf(user).slice(0, 1).toUpperCase();
}

/** 超管角色判断：只看角色摘要，不看权限码（权限码可能为空数组） */
export function isSuperuser(user: Pick<User, 'roles'>): boolean {
  return user.roles.some((role) => role.isSuperuser);
}
