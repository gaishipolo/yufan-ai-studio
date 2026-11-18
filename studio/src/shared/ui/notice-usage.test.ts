import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * 提示条不许手抄配色。
 *
 * 这个问题不是「样式不好看」，是**同一段样式抄了 8 份**：八处一模一样的
 * `border-[#fad7d4] bg-[#feeceb] text-[#d94036]`，另有三处用了
 * `border-danger/25 bg-danger/5`。色板里 `danger-line` / `danger-soft` /
 * `danger-deep` 本来就对得上这三段硬编码值 —— 色板是有的，`Notice` 也早就有，
 * 只是没人用。
 *
 * 为什么用**源码扫描**而不是逐个组件测试：
 * 逐个组件测试只能守住「已知的那些地方」，新写的弹窗照样会抄一份新的。
 * 源码扫描守的是「整个前端不许再出现这些硬编码」。
 *
 * 豁免：`shared/ui/Notice.tsx` 本身（它是配色来源），
 * 以及 `shared/ui/ConfirmDialog.tsx`（二次确认的提醒条有自己的紧凑排版，
 * 不是区域提示条，两者形态不同）。
 */

const SRC_ROOT = join(process.cwd(), 'src');

/** 这些硬编码色值一旦出现，就是有人又抄了一份提示条配色 */
const FORBIDDEN = [
  { name: '错误条边框', pattern: /border-\[#fad7d4\]/ },
  { name: '错误条底色', pattern: /bg-\[#feeceb\]/ },
  { name: '错误条文字', pattern: /text-\[#d94036\]/ },
  { name: '警告条边框', pattern: /border-\[#fbe8c8\]/ },
  { name: '警告条底色', pattern: /bg-\[#fff6e6\]/ },
  { name: '警告条文字', pattern: /text-\[#b97a10\]/ },
  /* 第四套配色（登录页与分配角色弹窗在用）：同样的语义、另一组值。
     它的存在说明漂移是**累积**的 —— 每加一处提示条就可能带出一套新配色。 */
  { name: '警告条底色（第四套）', pattern: /bg-\[#fff9ee\]/ },
  { name: '警告条文字（第四套）', pattern: /text-\[#8a5b00\]/ },
  /* 这套是「另一套配色」的变体：同样的语义、不同的值 */
  { name: '错误条（半透明变体）', pattern: /border-danger\/25/ },
  { name: '错误条底色（半透明变体）', pattern: /bg-danger\/5\b/ },
];

/** 这些文件是配色的**来源**，扫描它们只会找到它们自己 */
const EXEMPT = new Set([
  join('shared', 'ui', 'Notice.tsx'),
  join('shared', 'ui', 'ConfirmDialog.tsx'),
  join('shared', 'ui', 'notice-usage.test.ts'),
]);

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === 'node_modules' || entry === '.git') continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) walk(full, out);
    else if (/\.tsx?$/.test(entry)) out.push(full);
  }
  return out;
}

describe('提示条配色不许手抄', () => {
  const files = walk(SRC_ROOT).filter((file) => !EXEMPT.has(relative(SRC_ROOT, file)));

  it('扫得到源码文件（扫描器本身没坏）', () => {
    expect(files.length).toBeGreaterThan(50);
  });

  it.each(FORBIDDEN)('$name 没有再被手抄', ({ pattern }) => {
    const offenders: string[] = [];
    for (const file of files) {
      const source = readFileSync(file, 'utf8');
      if (!pattern.test(source)) continue;
      /* 注释里提一嘴色值不算抄 —— 真正的问题是 className 里出现它。
         判据：这一行同时含 `className` 或紧邻 JSX 属性位置。
         这里保守一点：只要出现就报，宁可误报也不要漏。 */
      offenders.push(relative(SRC_ROOT, file));
    }
    expect(offenders).toEqual([]);
  });
});
