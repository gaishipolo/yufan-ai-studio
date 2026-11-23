import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * 前端不许把「版本」写成 0。
 *
 * 素材库的乐观锁用 `expectedVersion` 传「客户端看到的资产版本」。旧契约里
 * `0` 是「不校验」的哨兵值，于是前端到处写 `expectedVersion: 0` 或
 * `asset.stateVersion ?? 0` —— 看着传了版本，实际把校验关掉了：
 * A 打开列表后 B 改了名，A 的改名/打标照样生效，没有任何提示
 * （REVIEW_2026-11-23 严重#1 / 重要#5）。
 *
 * 现在契约里 `0` **不是合法版本**（后端 422），版本只有两种表达：
 * - 一个真实版本（≥1）→ 做乐观锁校验；
 * - 字段不出现 → 明确不校验。
 *
 * 为什么要**扫源码**而不是逐个组件测：逐个组件测只能守住「已知的那几个
 * 地方」，新写的页面照样会再抄一个 `?? 0`。扫源码守的是「整个前端不许再出现」。
 *
 * 豁免：测试文件（里面可能故意构造 0 来验证后端会拒），以及本文件自身。
 */

const SRC_ROOT = join(process.cwd(), 'src');

/** 判定用的匹配：写死 0，或用 `?? 0` / `|| 0` 兜底 */
const FORBIDDEN = [
  /expectedVersions?\s*[:=]\s*0\b/,
  /expectedVersions?\s*[:=][^,;{}\n]*\?\?\s*0\b/,
  /expectedVersions?\s*[:=][^,;{}\n]*\|\|\s*0\b/,
];

const EXEMPT = new Set([join('shared', 'api', 'version-guard.test.ts')]);

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === 'node_modules' || entry === '.git') continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) walk(full, out);
    else if (/\.tsx?$/.test(entry)) out.push(full);
  }
  return out;
}

/**
 * 先把注释与字符串去掉再判定。
 *
 * 不去掉的话，注释里那句「原先固定传 0」就会被当成真代码 —— 注释与字符串
 * 里的字样不算调用。
 */
function stripCommentsAndStrings(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/(^|[^:])\/\/[^\n]*/g, '$1 ')
    .replace(/'(?:\\.|[^'\\])*'/g, "''")
    .replace(/"(?:\\.|[^"\\])*"/g, '""')
    .replace(/`(?:\\.|[^`\\])*`/g, '``');
}

describe('版本字段不许写成 0', () => {
  const files = walk(SRC_ROOT).filter(
    file =>
      !EXEMPT.has(relative(SRC_ROOT, file)) &&
      !/\.test\.tsx?$/.test(relative(SRC_ROOT, file)),
  );

  it('扫得到源码文件（扫描器本身没坏）', () => {
    expect(files.length).toBeGreaterThan(50);
  });

  it('没有 expectedVersion: 0 / ?? 0 这类写法', () => {
    const offenders: string[] = [];
    for (const file of files) {
      const source = stripCommentsAndStrings(readFileSync(file, 'utf8'));
      if (FORBIDDEN.some((pattern) => pattern.test(source))) {
        offenders.push(relative(SRC_ROOT, file));
      }
    }
    expect(offenders).toEqual([]);
  });
});
