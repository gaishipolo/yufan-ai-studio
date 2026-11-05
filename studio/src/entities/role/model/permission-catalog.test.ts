import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { PERMISSION_CODES, PERMISSION_GROUPS, isResolvablePermission } from './permission-catalog';

/* ------------------------------------------------------------------
 * 目录漂移检测
 *
 * 后端是唯一真相：各模块的 `ModuleSpec.permissions` 汇总成 `permission_catalog()`。
 * 前端这份常量只是 mock 模式下的镜像，**手抄的副本一定会过期**——
 * 上一版就漏了 `image:read:all` 与 `task:retry:any` 两项，而角色权限是整组替换，
 * 于是「在 mock 里保存一个真实环境带这两项的角色」会把它们当成未登记项清掉。
 *
 * 这里直接解析 `api/src/modules/**` 的 `PermissionSpec`（本仓库是 api + studio 双目录，
 * 读得到就比对；只有前端的检出会整体跳过）。不是"记得同步"，是"忘了就红"。
 * ------------------------------------------------------------------ */

/** vitest 的工作目录是 studio/，后端源码在同级的 api/ 下 */
const MODULES_DIR = resolve(process.cwd(), '../api/src/modules');

/** 从后端源码里抽出全部权限码：常量定义 + PermissionSpec 引用都覆盖 */
function readBackendCodes(dir: string): string[] {
  const files = (readdirSync(dir, { recursive: true }) as string[])
    .map((name) => join(dir, name))
    .filter((file) => file.endsWith('.py'));

  const codes = new Set<string>();

  for (const file of files) {
    const source = readFileSync(file, 'utf8');

    // 先收集 `PERM_READ: Final = "task:read"` 这类常量，供 PermissionSpec 引用解析
    const constants = new Map<string, string>();
    for (const match of source.matchAll(/^(PERM_[A-Z_]+)\s*(?::\s*Final)?\s*=\s*"([^"]+)"/gm)) {
      constants.set(match[1], match[2]);
    }

    for (const match of source.matchAll(/PermissionSpec\(\s*(?:"([^"]+)"|(PERM_[A-Z_]+))/g)) {
      const code = match[1] ?? constants.get(match[2]);
      // `${perm}:read` 这类脚手架占位符不在 modules 下，出现即说明扫描范围错了
      if (code) codes.add(code);
    }
  }

  return [...codes].sort();
}

describe('权限点目录', () => {
  it('没有重复的权限码', () => {
    expect(new Set(PERMISSION_CODES).size).toBe(PERMISSION_CODES.length);
  });

  it('每个分组都有内容', () => {
    for (const group of PERMISSION_GROUPS) {
      expect(group.items.length, `分组「${group.group}」是空的`).toBeGreaterThan(0);
    }
  });

  it('通配写法能解析到具体权限点，拼错的前缀解析不到', () => {
    expect(isResolvablePermission('*')).toBe(true);
    expect(isResolvablePermission('task:*')).toBe(true);
    expect(isResolvablePermission('task:read')).toBe(true);
    expect(isResolvablePermission('task*')).toBe(false);
    expect(isResolvablePermission('task:reed')).toBe(false);
  });

  /* 本仓库里 api/ 与 studio/ 是同级目录；单独检出 studio 时这条自动跳过 */
  it.skipIf(!existsSync(MODULES_DIR))('与后端 ModuleSpec 逐条一致', () => {
    expect(readBackendCodes(MODULES_DIR)).toEqual(PERMISSION_CODES);
  });
});
