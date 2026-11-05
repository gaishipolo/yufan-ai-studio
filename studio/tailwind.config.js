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
        ok: '#17c07a',
        warn: '#f5a623',
        danger: '#f2544b',
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
      },
      animation: {
        fadeIn: 'fadeIn .18s ease',
        popIn: 'popIn .2s cubic-bezier(.22,.61,.36,1)',
        slideDown: 'slideDown .18s ease',
      },
    },
  },
  plugins: [],
};
