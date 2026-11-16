/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        canvas: '#eaf1f3',
        'canvas-deep': '#e3edef',
        panel: '#ffffff',
        'panel-soft': '#f7fbfc',
        ink: {
          DEFAULT: '#1d2b33',
          soft: '#4a5c66',
          mute: '#93a4ad',
        },
        line: {
          DEFAULT: '#e2ebee',
          soft: '#eef3f5',
          strong: '#d3e0e5',
        },
        // `brand`（青）是**在飞**的信号色：生成中、轮询中、进度条的头端。
        // 它不再是装饰色 —— 以前它只当区块标题前面那颗小圆点用，
        // 那是「假装有意义」的装饰；现在只有真在跑的东西才用它。
        brand: {
          DEFAULT: '#12c4c8',
          deep: '#0ea9ad',
          soft: '#e4f9fa',
          line: '#b3ecee',
        },
        primary: {
          DEFAULT: '#2b7cff',
          deep: '#1a6cf0',
          soft: '#e9f1ff',
          line: '#b9d3ff',
        },
        // 语义色的三段式。**保持 `ok` 是字符串**：已有 CSS 用 `theme('colors.ok / 8%')`
        // 做半透明描边，改成嵌套对象会让 `theme('colors.ok')` 直接解析失败。
        ok: '#17c07a',
        'ok-deep': '#0f9c63',
        'ok-soft': '#e8faf2',
        'ok-line': '#d3f3e4',
        warn: '#f5a623',
        'warn-deep': '#b97a10',
        'warn-soft': '#fff6e6',
        'warn-line': '#fbe8c8',
        danger: '#f2544b',
        'danger-deep': '#d94036',
        'danger-soft': '#feeceb',
        'danger-line': '#fad7d4',
        violet: '#7c5cff',
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
      },
      /* 等宽字：只给**数据**用 —— 时长、画幅、计数、时间码、模型代号。
         视频这条线上的东西大多是可以竖着扫的一列数字，等宽 + tabular-nums
         让那一列对齐，于是「哪条片子最长」不用逐行读，扫一眼就知道。 */
      fontFamily: {
        sans: [
          '-apple-system',
          'BlinkMacSystemFont',
          '"Segoe UI"',
          '"PingFang SC"',
          '"Hiragino Sans GB"',
          '"Microsoft YaHei"',
          'sans-serif',
        ],
        mono: [
          'ui-monospace',
          'SFMono-Regular',
          '"SF Mono"',
          'Menlo',
          'Consolas',
          '"Liberation Mono"',
          'monospace',
        ],
      },
      borderRadius: {
        card: '12px',
        panel: '16px',
        pill: '999px',
      },
      boxShadow: {
        card: '0 6px 18px rgba(21, 50, 61, .08)',
        float: '0 8px 22px rgba(21, 50, 61, .10)',
        pop: '0 18px 44px rgba(21, 50, 61, .16)',
        ring: '0 0 0 3px rgba(43, 124, 255, .08)',
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
