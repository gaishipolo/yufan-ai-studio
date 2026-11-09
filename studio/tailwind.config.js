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
        brand: {
          DEFAULT: '#12c4c8',
          deep: '#0ea9ad',
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
      fontSize: {
        '2xs': ['10px', '14px'],
        '3xs': ['9.5px', '12px'],
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
        // 右侧滑出（详情浮层）。位移只有 24px：滑 300px 会让"按下到看见"的
        // 时间花在动画上，而用户点的是"给我看这张图"
        slideInRight: {
          from: { opacity: '0', transform: 'translateX(24px)' },
          to: { opacity: '1', transform: 'translateX(0)' },
        },
      },
      animation: {
        fadeIn: 'fadeIn .18s ease',
        popIn: 'popIn .2s cubic-bezier(.22,.61,.36,1)',
        slideDown: 'slideDown .18s ease',
        slideInRight: 'slideInRight .22s cubic-bezier(.22,.61,.36,1)',
      },
    },
  },
  plugins: [],
};
