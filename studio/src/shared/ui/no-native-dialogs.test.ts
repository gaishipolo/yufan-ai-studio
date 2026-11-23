import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * 前端不许用浏览器原生弹窗（`alert` / `confirm` / `prompt`）。
 *
 * 原生弹窗三个问题：**样式不受控**（各系统各一套，跟界面完全不是一回事）、
 * **阻塞**（整页卡住，用户连滚动都做不了）、**不能分类**（成功和失败长得
 * 一样，只能靠文字区分）。
 *
 * 替代品都在 `shared/ui` 里：
 * - 通知 → `shared/lib/toast.ts` 的 `toast()`（页面内浮层，不阻塞）
 * - 需要确认 → `shared/ui/ConfirmDialog.tsx`（页面内弹层，能摊开事实清单）
 * - 就地状态 → `shared/ui/Notice.tsx`（表单/卡片里的常驻提示条）
 *
 * 为什么要**扫源码**而不是逐个组件测：逐个组件测只能守住「已知的那几个
 * 地方」，新写的页面照样会再抄一个 `window.confirm`。扫源码守的是「整个
 * 前端不许再出现」。
 *
 * 豁免：测试文件（里面可能出现 `javascript:alert(1)` 这类**字符串**），
 * 以及本文件自身。
 */

const SRC_ROOT = join(process.cwd(), 'src');

/** 判定用的匹配：`window.alert(` 与裸的 `alert(` 都算 */
const FORBIDDEN = /(?:^|[^\w.])(window\s*\.\s*)?(alert|confirm|prompt)\s*\(/;

const EXEMPT = new Set([join('shared', 'ui', 'no-native-dialogs.test.ts')]);

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
 * 不去掉的话，`ConfirmDialog.tsx` 里那句「与普通 `window.confirm` 的分界」
 * 的注释就会被当成真调用 —— 注释与字符串里的字样不算调用。
 */
function stripCommentsAndStrings(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, ' ')
    .replace(/(^|[^:])\/\/[^\n]*/g, '$1 ')
    .replace(/'(?:\\.|[^'\\])*'/g, "''")
    .replace(/"(?:\\.|[^"\\])*"/g, '""')
    .replace(/`(?:\\.|[^`\\])*`/g, '``');
}

describe('不用浏览器原生弹窗', () => {
  const files = walk(SRC_ROOT).filter(
    file =>
      !EXEMPT.has(relative(SRC_ROOT, file)) &&
      !/\.test\.tsx?$/.test(relative(SRC_ROOT, file)),
  );

  it('扫得到源码文件（扫描器本身没坏）', () => {
    expect(files.length).toBeGreaterThan(50);
  });

  it('没有 alert / confirm / prompt 的原生调用', () => {
    const offenders: string[] = [];
    for (const file of files) {
      const source = stripCommentsAndStrings(readFileSync(file, 'utf8'));
      if (!FORBIDDEN.test(source)) continue;
      offenders.push(relative(SRC_ROOT, file));
    }
    expect(offenders).toEqual([]);
  });
});
