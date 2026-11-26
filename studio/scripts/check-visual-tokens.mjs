/* 视觉令牌守卫：圆角不许写死，内容类徽章不许用蓝色。
 *
 *   node scripts/check-visual-tokens.mjs
 *
 * ## 为什么要有这条守卫
 *
 * 圆角写死成 `rounded-[9px]` 的时候，没人知道那个 9 是「比控件大一点、
 * 比卡片小一点」还是随手打的 —— 于是同一个界面里出现了 5、7、9、10、14
 * 五种圆角，而每一处单独看都说得过去。这就是「约定写在注释里但没有机制
 * 执行」的典型样子：规范里写着「圆角从 token 取」，代码里却一片随手值。
 *
 * ## 两条规矩
 *
 * 1. **圆角只能来自刻度**：类名走 tailwind.config.js 的圆角刻度，
 *    CSS 里写 `border-radius` 也只能落在同一套刻度上（滚动条那两处
 *    以前是裸的 8px / 999px，肉眼看不见，但规矩是「不许出现刻度外的值」）。
 *
 * 2. **颜色预算**：近黑 = 行动 · 蓝 = 选中/身份标记 · 青 = 在飞 · 红 = 失败，
 *    其余（模型名、类型、版本号、计数）一律中性。内容类徽章写成
 *    `<Chip tone="primary">` 就是违反预算 —— 一屏四张卡就有四块蓝底，
 *    真正该被看见的「生成中」反而被淹了。蓝色徽章只留给身份标记
 *    （比如「我」），并且必须登记在下面的白名单里。
 *
 * ## 扫描方式：整文件，不是逐行
 *
 * 逐行扫描有两个盲区，都真实发生过：
 *   - `<Chip key={kind} tone="primary">` —— `tone` 不是第一个属性就漏；
 *   - `<Chip\n  tone="primary"\n>` —— 属性换行就漏。
 * 所以这里对**整个文件**跑正则，再按匹配位置换算行号。
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';

const root = resolve(process.cwd(), 'src');
const SCAN_EXT = new Set(['.ts', '.tsx', '.css']);
const SKIP_DIR = new Set(['node_modules', 'dist', 'coverage']);

/* 圆角刻度（与 tailwind.config.js 的 borderRadius 保持一致）：
   2 角标 · 3 按钮 · 4 卡片/面板 · 999(pill) · 9999(full) · 0
   纸感编辑部版刻度几乎没有弧度 —— 弧度越大越像后台模板。 */
const RADIUS_SCALE = new Set([0, 2, 3, 4, 999, 9999]);

/* 蓝色徽章的白名单：只放「身份标记」—— 蓝色徽章唯一合理的用途。
   新增前先问：它表示「这是谁/这是我的」，还是「这是内容」？
   后者一律用中性 chip。 */
const CHIP_BLUE_ALLOWLIST = ['src/pages/user-access/UsersSection.tsx'];

const RULES = [
  {
    label: '写死的圆角',
    pattern: /rounded-\[(\d+(?:\.\d+)?(?:px|rem|em|%))\]/g,
    hint: '圆角取自 tailwind.config.js 的刻度：rounded-xs / sm / md / lg / xl / 2xl / card / panel / hero / pill / full',
  },
  {
    label: '内容类徽章用了强调色',
    /* `<Chip` 后面允许有别的属性，也允许换行 —— 只匹配「属性名叫 tone
       且值是 primary」这一件事，不关心它排第几个。 */
    pattern: /<Chip\b[^>]*\btone="primary"/g,
    hint: '内容（模型名、类型、版本、计数）用中性 <Chip>；墨色与朱红只留给行动、选中与状态，且必须登记在守卫白名单里',
    allowlist: CHIP_BLUE_ALLOWLIST,
  },
  {
    label: 'CSS 里的裸圆角',
    pattern: /border-radius:\s*(\d+(?:\.\d+)?)px/g,
    hint: 'CSS 里的 border-radius 也要落在刻度上（4/6/8/12/16/20/24/32/999）',
    /* 值在刻度上就放过 —— 8px 是 rounded-lg、999px 是 pill，都合法 */
    validate: (match) => !RADIUS_SCALE.has(Number(match[1])),
  },
];

const files = [];
(function walk(dir) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      if (!SKIP_DIR.has(entry)) walk(full);
      continue;
    }
    if (SCAN_EXT.has(full.slice(full.lastIndexOf('.')))) files.push(full);
  }
})(root);

const offenders = [];
for (const file of files) {
  const source = readFileSync(file, 'utf8');
  const relPath = relative(process.cwd(), file).replace(/\\/g, '/');
  for (const rule of RULES) {
    if (rule.allowlist?.includes(relPath)) continue;
    rule.pattern.lastIndex = 0;
    let match = rule.pattern.exec(source);
    while (match) {
      if (!rule.validate || rule.validate(match)) {
        offenders.push({
          file: relPath,
          line: source.slice(0, match.index).split(/\r?\n/).length,
          label: rule.label,
          snippet: match[0].replace(/\s+/g, ' ').trim(),
          hint: rule.hint,
        });
      }
      match = rule.pattern.exec(source);
    }
  }
}

if (offenders.length > 0) {
  console.error('视觉令牌违规：\n');
  for (const item of offenders) {
    console.error(`  ${item.file}:${item.line}  ${item.label}  ${item.snippet}`);
    console.error(`      → ${item.hint}`);
  }
  console.error(`\n共 ${offenders.length} 处。圆角必须落在刻度上；内容类徽章必须中性。`);
  process.exitCode = 1;
} else {
  console.log(
    `视觉令牌检查通过（${files.length} 个文件：圆角全部落在刻度上，内容类徽章没有占用强调色）`,
  );
}
