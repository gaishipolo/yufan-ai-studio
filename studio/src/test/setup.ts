import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { afterEach } from 'vitest';

/**
 * 测试环境统一收尾。
 * RTL 的自动 cleanup 只在开启 vitest globals 时生效，这里显式关闭，
 * 避免「上一个用例的 DOM 残留导致 getByRole 命中多个」这类假失败。
 */
afterEach(() => {
  cleanup();
});
