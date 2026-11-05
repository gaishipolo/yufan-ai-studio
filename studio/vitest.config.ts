import { fileURLToPath } from 'node:url';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

/**
 * 测试配置与 vite.config.ts 分开：
 * 构建配置只服务打包，测试配置独立演进（环境、setup、覆盖率）。
 * alias 必须与 tsconfig 的 paths 保持一致，否则测试里 `@/` 解析不到。
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    // jsdom 测试没有原生模块依赖，用线程池比 fork 子进程更快，
    // 也避开子进程写临时缓存时的权限问题（曾导致测试文件被静默跳过）
    pool: 'threads',
    // 组件测试不需要真实 CSS 编译，跳过可显著提速
    css: false,
    restoreMocks: true,
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html'],
      include: ['src/**/*.{ts,tsx}'],
      exclude: [
        'src/**/*.test.{ts,tsx}',
        'src/test/**',
        'src/mocks/**',
        'src/main.tsx',
        'src/vite-env.d.ts',
      ],
    },
  },
});
