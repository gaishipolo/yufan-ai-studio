import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { APP_NAME, HOME_PATH, LOGIN_PATH } from './app';

/* ------------------------------------------------------------------
 * 产品名只有两个落点，而且必须对得上：
 *   - `APP_NAME`      → 顶栏、登录页（界面里的字标）
 *   - `index.html` 的 <title> → 浏览器标签页
 * 曾经还有第三个（`.env` 的 `VITE_APP_TITLE`），全仓无人读取，
 * 于是"改名"时最容易被改错的就是它。那一个已经删掉了，这里钉住剩下的两个。
 * ------------------------------------------------------------------ */

const root = (relative: string) => new URL(relative, import.meta.url);

function readStudioFile(relative: string): string {
  return readFileSync(root(`../../../${relative}`), 'utf8');
}

describe('产品名', () => {
  it('浏览器标签标题包含界面里的产品名', () => {
    const html = readStudioFile('index.html');
    const title = /<title>([^<]*)<\/title>/i.exec(html)?.[1]?.trim() ?? '';

    // 标签页里带品牌前缀（「雨范 AI 短视频工作台」），界面上品牌由 logo 承担，
    // 所以是「包含」而不是「相等」
    expect(title).toContain(APP_NAME);
  });

  it('没有第二个"看起来能改名"的开关', () => {
    for (const file of ['.env.development', '.env.production']) {
      expect(readStudioFile(file), `${file} 里又出现了 VITE_APP_TITLE`).not.toContain(
        'VITE_APP_TITLE',
      );
    }
  });
});

describe('骨架路径常量', () => {
  it('首页与登录页都是绝对路径（守卫直接用它们 Navigate）', () => {
    for (const path of [HOME_PATH, LOGIN_PATH]) {
      expect(path.startsWith('/')).toBe(true);
    }
    expect(HOME_PATH).not.toBe(LOGIN_PATH);
  });
});
