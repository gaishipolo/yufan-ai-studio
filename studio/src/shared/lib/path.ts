/**
 * 路径匹配：导航高亮、分组定位共用一套规则。
 *
 * `isPathActive('/tasks/abc', '/tasks')` → true
 * `isPathActive('/tasks', '/task')`      → false（不做前缀误伤）
 */
export function isPathActive(pathname: string, target: string): boolean {
  if (!target) return false;
  return pathname === target || pathname.startsWith(`${target}/`);
}
