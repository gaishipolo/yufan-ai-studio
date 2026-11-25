import { can } from '@/shared/access/evaluate';
import { HOME_PATH } from '@/shared/config/app';
import type { Subject } from '@/shared/access/types';
import type { AppRegistry } from './types';

/**
 * 登录后落到哪个页面：用户**实际能打开**的第一个导航项。
 *
 * 判据必须与侧栏完全一致 —— 都走 `navGroups` 的顺序与 `can(subject, permissions)`，
 * 否则会出现「跳到第一页，侧栏里却排在第二」「侧栏第一项有权限，落地页却是 403」
 * 这类漂移。`navGroups` 由 `createRegistry` 按 groupOrder / order 排好，这里不再排序。
 *
 * 没有可见导航项（用户一个页面权限都没有）时回退 `HOME_PATH`：此时没有任何
 * 「能访问」的页面可选，落地到固定的首页由权限守卫给出明确的 403 与所需权限，
 * 比跳一个编出来的地址更可解释。
 */
export function firstAccessiblePath(
  subject: Subject | null | undefined,
  registry: AppRegistry,
): string {
  for (const group of registry.navGroups) {
    for (const item of group.items) {
      if (can(subject, item.permissions)) return item.path;
    }
  }
  return HOME_PATH;
}
