/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        /* ── 纸：三层，从外到里 ──────────────────────────────
           canvas  垫板（页面之外那圈纸，比台面暗一档）
           surface 台面（AppShell 的 main，工作台本身）
           panel   卡片与面板（近白纸，永远浮在台面之上）
           纸感的关键不在「浅」，在**暖**：冷白（#fff / #f7f8fa）
           一看就是后台模板，米黄偏灰的纸才像纸。 */
        canvas: '#e7e2d5',
        'canvas-deep': '#ddd7c7',
        surface: '#f2eee4',
        panel: '#fbf9f4',
        'panel-soft': '#f7f4ec',
        /* ── 墨：文字与实心块 ────────────────────────────────
           近黑但偏暖（#17150f，像铅字油墨，不是纯黑的数码感）。 */
        ink: {
          DEFAULT: '#17150f',
          soft: '#4a443a',
          mute: '#948b78',
        },
        line: {
          DEFAULT: '#ded7c6',
          soft: '#eae4d6',
          strong: '#c0b6a0',
        },
        /* ── 墨黑：行动与选中 ───────────────────────────────
           纸感编辑部里没有「蓝色按钮」这种东西：主动作是墨块，
           当前所在也是墨块（反白）。这是印刷逻辑，不是界面逻辑。 */
        primary: {
          DEFAULT: '#17150f',
          deep: '#000000',
          soft: '#e9e4d7',
          line: '#c0b6a0',
        },
        /* `cta` = 行动色（主按钮用，留一个语义名）。 */
        cta: {
          DEFAULT: '#17150f',
          deep: '#000000',
        },
        /* ── 朱红：全站唯一的彩色 ──────────────────────────
           它不表示「品牌」，只表示**这里需要注意**：正在跑、
           失败了、排队了、报错了。纸面上只有一种颜色时，
           颜色一出现就是信息本身 —— 这是编辑部的老办法：
           不用颜色分类，只用颜色喊话。 */
        brand: {
          DEFAULT: '#b0391c',
          deep: '#8f2c14',
          soft: '#f8eae5',
          line: '#e3bfb1',
        },
        /* 语义色的三段式。**保持 `ok` 是字符串**：已有 CSS 用
           `theme('colors.ok / 8%')` 做半透明描边，改成嵌套对象会让
           `theme('colors.ok')` 直接解析失败。 */
        ok: '#3f6b4f',
        'ok-deep': '#2f5239',
        'ok-soft': '#eef1ea',
        'ok-line': '#d2dbcd',
        warn: '#8a6a2f',
        'warn-deep': '#6b5122',
        'warn-soft': '#f6f0e1',
        'warn-line': '#e4d8ba',
        danger: '#b0391c',
        'danger-deep': '#8f2c14',
        'danger-soft': '#f8eae5',
        'danger-line': '#e3bfb1',
        /* 引用高亮（模板里的 {{slot}} 与 @提及）：暖褐，不跟朱红抢。 */
        quote: '#7a5c3a',
      },
      /* 字阶：六级，每级差得开，**不靠尺寸区分意思**。
         以前全仓有 18 个字号（10 / 10.5 / 11 / 11.5 / 12 / 12.5 / 13 / 13.5 /
         14 …），其中 239 处挤在 10–11.5px 之间 —— 标题和说明肉眼分不出，
         「让布局自己解释」就无从谈起。

         现在：
           micro 10  角标、计数、版权行
           meta  11  时间戳、辅助信息
           xs    12  紧凑控件与次要正文
           body  13  正文（body 的声明值一直是 13px，之前被组件一路压下去）
           lead  15  区块标题
           title 19  页面标题
         行高统一 1.5（与 body 的 line-height 同源），不再每处另写。 */
      fontSize: {
        micro: ['10px', { lineHeight: '1.5' }],
        meta: ['11px', { lineHeight: '1.5' }],
        xs: ['12px', { lineHeight: '1.5' }],
        body: ['13px', { lineHeight: '1.55' }],
        lead: ['15px', { lineHeight: '1.45' }],
        title: ['19px', { lineHeight: '1.35' }],
        display: ['26px', { lineHeight: '1.3' }],
        /* 页头标题：26px。
           19px 的标题在 13px 正文旁边只大 1.5 倍，一屏扫过去分不出
           「这是标题」和「这是一行字」——层级建立不起来，
           页面就只剩「一坨内容」。26px / 13px ≈ 2 倍，标题才站得住。 */
        h1: ['30px', { lineHeight: '1.2' }],
        /* 统计数字：34px，一页只给一个。
           等宽 + tabular-nums，所以它既是视觉焦点，也是一列能竖着扫的数。 */
        stat: ['34px', { lineHeight: '1.05' }],
      },
      /* 等宽字：只给**数据**用 —— 时长、画幅、计数、时间码、模型代号。
         视频这条线上的东西大多是可以竖着扫的一列数字，等宽 + tabular-nums
         让那一列对齐，于是「哪条片子最长」不用逐行读，扫一眼就知道。 */
      fontFamily: {
        /* 正文：自托管思源黑体。不跟系统字体走 —— 同一份稿子在
           mac / windows / 容器里长得不一样，就没法当版来看。 */
        sans: [
          '"Noto Sans SC"',
          '-apple-system',
          'BlinkMacSystemFont',
          '"Segoe UI"',
          '"PingFang SC"',
          '"Hiragino Sans GB"',
          '"Microsoft YaHei"',
          'sans-serif',
        ],
        /* 标题：思源宋体。纸感编辑部的地基 —— 衬线大标题 + 无衬线正文
           + 等宽数据，是印刷排版的三件套，不是界面控件的三件套。 */
        serif: [
          '"Noto Serif SC"',
          '"Songti SC"',
          'SimSun',
          'serif',
        ],
        mono: [
          '"JetBrains Mono"',
          '"Noto Sans SC"',
          'ui-monospace',
          'SFMono-Regular',
          '"SF Mono"',
          'Menlo',
          'Consolas',
          '"Liberation Mono"',
          'monospace',
        ],
      },
      /* 圆角刻度 —— 与 docs/CODE_STYLE.md §3.5.1 的刻度表一致
         （Tailwind 的档位刚好等于那套刻度，只是名字错开一档，
         所以「控件 8」在 Tailwind 里叫 lg）：
           4  角标、代码片        → rounded-xs
           6  紧凑控件、行内按钮  → rounded-sm / rounded-md
           8  输入框、菜单行、按钮底座 → rounded-lg
           12 白卡、提示条、空态块  → rounded-xl / rounded-card
           16 面板、特性卡、弹窗    → rounded-2xl / rounded-panel
           32 主推卡（唯一允许的大圆角）→ rounded-hero
           999 按钮、页签、徽章（pill）→ rounded-pill
         刻度之外一律不写 rounded-[Npx]：写死值就是「这里的圆角没想清楚」。
         圆角承担语义 —— 控件收着、卡片放开、行动收成 pill，
         靠半径差就能分辨「这是能点的」还是「这是内容」。 */
      /* 圆角刻度 —— 纸感编辑部版：几乎全是直角。
         2  角标、徽章、代码片
         3  按钮、输入框、页签
         4  卡片、面板、弹窗
         999 圆形（头像、进度条、开关）—— 圆形只留给本来就是圆的东西。
         纸感来自「直角 + 发丝线」，不是「更圆」。 */
      borderRadius: {
        xs: '2px',
        sm: '3px',
        md: '3px',
        lg: '4px',
        xl: '4px',
        '2xl': '4px',
        '3xl': '4px',
        hero: '32px',
        card: '4px',
        panel: '4px',
        pill: '999px',
      },
      /* 阴影只留给「压在别的东西上面」的东西：浮层、弹窗、悬浮预览。
         面板、卡片、按钮一律扁平 + 发丝线 —— 投影一旦铺到白卡上，
         整屏就浮起来：密排的卡片互相干扰，读起来像一堆盒子而不是一张台面。
         （见 docs/CODE_STYLE.md §3.5.1：flat-with-borders 是默认，阴影是例外。） */
      /* 纸不投影，靠色阶与发丝线分层。
         阴影只留给「压在纸面上」的东西：浮层、弹窗、悬浮预览。 */
      boxShadow: {
        card: '0 1px 0 rgba(23, 21, 15, .03)',
        float: '0 4px 12px rgba(23, 21, 15, .08)',
        pop: '0 16px 36px rgba(23, 21, 15, .12)',
        ring: '0 0 0 3px rgba(23, 21, 15, .10)',
      },
      spacing: {
        4.5: '1.125rem',
      },
      transitionTimingFunction: {
        smooth: 'cubic-bezier(.22, .61, .36, 1)',
      },
      keyframes: {
        fadeIn: {
          from: { opacity: '0' },
          to: { opacity: '1' },
        },
        popIn: {
          from: { opacity: '0', transform: 'translateY(10px) scale(.98)' },
          to: { opacity: '1', transform: 'translateY(0) scale(1)' },
        },
        slideDown: {
          from: { opacity: '0', transform: 'translateY(-6px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        /* 朝上弹的浮层：起点在下方，往上收。
           方向反了的话，浮层会先往下坠一下再归位 —— 像是卡了一下。 */
        slideUp: {
          from: { opacity: '0', transform: 'translateY(6px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
      },
      animation: {
        fadeIn: 'fadeIn .18s ease',
        popIn: 'popIn .2s cubic-bezier(.22,.61,.36,1)',
        slideDown: 'slideDown .18s ease',
        slideUp: 'slideUp .18s ease',
      },
    },
  },
  plugins: [],
};
