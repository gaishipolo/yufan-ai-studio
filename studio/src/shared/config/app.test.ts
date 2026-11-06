import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  APP_BRAND,
  APP_BRAND_EN,
  APP_DESCRIPTION,
  APP_FULL_NAME,
  APP_NAME,
  APP_VERSION,
  HOME_PATH,
  LOGIN_PATH,
} from './app';

/* ------------------------------------------------------------------
 * 品牌文案只有 `shared/config/app.ts` 一个来源。
 *
 * `index.html` 是静态 HTML，没法 import TS，所以那里保留同样的字面量，
 * 由下面的相等比对钉住；界面组件里再出现品牌字面量会直接红。
 * 曾经还有第三个「看起来能改名」的开关 `.env` 的 `VITE_APP_TITLE`，
 * 全仓无人读取，已删除，下面防止它复活。
 * ------------------------------------------------------------------ */

const root = (relative: string) => new URL(relative, import.meta.url);

function readStudioFile(relative: string): string {
  return readFileSync(root(`../../../${relative}`), 'utf8');
}

function collectTsx(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) return collectTsx(full);
    return entry.isFile() && /\.tsx$/.test(entry.name) && !/\.test\.tsx$/.test(entry.name)
      ? [full]
      : [];
  });
}

describe('产品名', () => {
  it('浏览器标签标题 = 完整产品名', () => {
    const html = readStudioFile('index.html');
    const title = /<title>([^<]*)<\/title>/i.exec(html)?.[1]?.trim() ?? '';
    expect(title).toBe(APP_FULL_NAME);
  });

  it('meta description 与常量一致', () => {
    const html = readStudioFile('index.html');
    const description = /<meta name="description" content="([^"]*)"/i.exec(html)?.[1] ?? '';
    expect(description).toBe(APP_DESCRIPTION);
  });

  it('完整产品名由字标与产品名拼成，不是另写一份', () => {
    expect(APP_FULL_NAME).toBe(`${APP_BRAND} ${APP_NAME}`);
  });

  it('界面组件里不再硬编码品牌文案', () => {
    const literals = [APP_BRAND, APP_BRAND_EN, APP_NAME, APP_VERSION];
    for (const file of collectTsx(join(process.cwd(), 'src'))) {
      const source = readFileSync(file, 'utf8');
      for (const literal of literals) {
        expect(source, `${file} 里仍写着「${literal}」，请改用 shared/config/app 常量`).not.toContain(
          literal,
        );
      }
    }
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
