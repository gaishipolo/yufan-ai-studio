/* 下载并自托管三套字体（需要外网）。
 *
 *   node scripts/fetch-fonts.mjs
 *
 * 产出：
 *   public/fonts/<family>/**.woff2   字体分片
 *   src/styles/fonts.css              @font-face 声明（src 指向 /fonts/…）
 *
 * 为什么自托管而不是引 CDN：产品跑在内网/离线环境，外链 CDN 会拿不到字体，
 * 标题直接掉回系统字体 —— 而标题字体正是这套「纸感编辑部」视觉的地基。
 * 中文按 unicode-range 切片，浏览器只下载页面实际用到的那几片（单片几 KB）。
 *
 * 字体与授权：
 *   Noto Serif SC / Noto Sans SC — SIL Open Font License 1.1
 *   JetBrains Mono              — SIL Open Font License 1.1
 */
import { mkdirSync, writeFileSync, existsSync } from 'node:fs';
import { join, resolve } from 'node:path';

const ROOT = resolve(process.cwd());
const PKGS = [
  { pkg: '@fontsource-variable/noto-serif-sc', dir: 'noto-serif-sc', family: 'Noto Serif SC' },
  { pkg: '@fontsource-variable/noto-sans-sc', dir: 'noto-sans-sc', family: 'Noto Sans SC' },
  { pkg: '@fontsource-variable/jetbrains-mono', dir: 'jetbrains-mono', family: 'JetBrains Mono' },
];

const blocks = [];
let totalBytes = 0;

for (const p of PKGS) {
  const cssUrl = 'https://cdn.jsdelivr.net/npm/' + p.pkg + '/index.css';
  const css = await (await fetch(cssUrl)).text();
  const dir = join(ROOT, 'public/fonts', p.dir);
  mkdirSync(dir, { recursive: true });

  const refs = [...new Set([...css.matchAll(/url\(\.\/files\/([^)'"]+\.woff2)\)/g)].map((m) => m[1]))];

  let fetched = 0;
  for (const file of refs) {
    const target = join(dir, file);
    if (existsSync(target)) { fetched++; continue; }
    const res = await fetch('https://cdn.jsdelivr.net/npm/' + p.pkg + '/files/' + file);
    if (!res.ok) continue;
    const buf = Buffer.from(await res.arrayBuffer());
    writeFileSync(target, buf);
    totalBytes += buf.length;
    fetched++;
  }

  /* 三件事：把 ./files/x.woff2 改成 /fonts/<dir>/x.woff2（文件落在 public/ 下，
     走绝对路径，构建时原样拷贝到站点根）；去掉 woff 回退（woff2 覆盖足够）；
     去掉 fontsource 的 " Variable" 后缀，让 Tailwind 里的字体名更好读。 */
  const rewritten = css
    .replace(/url\(\.\/files\/([^)'"]+\.woff2)\)/g, (_m, file) => 'url(/fonts/' + p.dir + '/' + file + ')')
    .replace(/url\(\.\/files\/[^)'"]+\.woff\)/g, '')
    .replaceAll(p.family + ' Variable', p.family);

  blocks.push('/* ===== ' + p.family + '（' + p.pkg + '，SIL OFL 1.1）===== */\n' + rewritten);
  console.log(p.family + ': ' + fetched + ' 个分片');
}

mkdirSync(join(ROOT, 'src/styles'), { recursive: true });
writeFileSync(join(ROOT, 'src/styles/fonts.css'), blocks.join('\n\n'), 'utf8');
console.log('新增字体字节: ' + (totalBytes / 1024 / 1024).toFixed(2) + ' MB');
