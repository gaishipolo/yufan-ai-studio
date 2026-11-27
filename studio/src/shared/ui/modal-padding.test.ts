import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * 弹窗内容必须离边有缝隙。
 *
 * `Modal` 组件只给标题栏加了 `px-[18px]`，`children` 区域本身**没有内边距**
 * —— 全靠每个调用方在自己的首层容器上加 `p-[18px]` / `px-[18px] py-4`。
 * 漏加的表现是表单、候选网格整个贴着弹窗边框（截图里一眼就能看出来丑），
 * 而且逐个组件测试守不住「新写的弹窗」—— 所以这里用源码扫描。
 *
 * 为什么不在 Modal 里统一加：35 个调用点里 31 个已经自己加了，
 * 在 Modal 里再加一遍会变成双倍间距。约束「调用方必须加」cost 更低。
 *
 * 豁免：`Modal.test.tsx`（测试自己造的最小内容）、`ReferenceImages.tsx`
 * （内容就是一张图片，靠图片自身留白）。
 */

const SRC_ROOT = join(process.cwd(), 'src');
const EXEMPT = new Set([
  'shared/ui/Modal.test.tsx',
  'shared/ui/ReferenceImages.tsx',
]);

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) walk(path, out);
    else if (name.endsWith('.tsx')) out.push(path);
  }
  return out;
}

/** 首层容器上有横向 padding 吗（p-x 或四边 p 都算）。 */
const HAS_PADDING = /\b(?:p|px)-\[?\d+(?:px)?\]?/;

describe('弹窗内容区必须离边', () => {
  it('每个 <Modal> 的首层内容容器都有内边距', () => {
    const offenders: string[] = [];
    for (const file of walk(SRC_ROOT)) {
      const rel = relative(SRC_ROOT, file).replace(/\\/g, '/');
      if (EXEMPT.has(rel)) continue;
      const source = readFileSync(file, 'utf-8');
      const open = source.matchAll(/<Modal\b/g);
      for (const match of open) {
        const gt = source.indexOf('>', match.index);
        if (gt < 0) continue;
        // 首层子元素：跳过 JSX 注释与空白
        const tail = source.slice(gt + 1, gt + 600);
        const first = /<([a-zA-Z][\w.]*)([^>]*?)>/.exec(tail);
        if (!first) continue;
        if (!HAS_PADDING.test(first[2])) {
          offenders.push(`${rel}: <${first[1]}>`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });
});
