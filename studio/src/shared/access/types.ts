/* ------------------------------------------------------------------
 * 访问控制骨架的类型
 *
 * 只定义「谁 / 有什么权限 / 能不能做」，不定义「权限从哪来」：
 * 登录模块负责把后端返回的 subject 写进 session store，骨架自动生效。
 * ------------------------------------------------------------------ */

export type Permission = string;

export interface Subject {
  id: string;
  name: string;
  /** 角色仅作展示与业务判断用；真正的门禁一律走 permissions */
  roles: string[];
  /** 由后端下发，如 ['task:read', 'user:manage']；'*' 表示全量 */
  permissions: Permission[];
}

export const GUEST_SUBJECT: Subject = Object.freeze({
  id: 'anonymous',
  name: '未登录',
  roles: ['guest'],
  permissions: [],
});
