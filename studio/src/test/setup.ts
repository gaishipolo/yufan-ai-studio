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

/**
 * 补一个内存版 localStorage。
 *
 * Node 22 起自带 Web Storage：`localStorage` 在没有 `--localstorage-file` 时
 * 是个**存在但为 undefined** 的全局，正好挡在 jsdom 的实现前面 —— 于是
 * `window.localStorage` 是空的，用到它的用例（侧栏折叠状态、登录 token…）
 * 会在 `beforeEach` 里直接炸掉，一批文件整片红。
 *
 * 只在 jsdom 真的没给出 localStorage 时才装，正常环境不受影响。
 */
function ensureLocalStorage(): void {
  try {
    if (window.localStorage) return;
  } catch {
    // 不透明源下访问 localStorage 会直接抛，落到底下重建
  }

  const store = new Map<string, string>();
  const shim: Storage = {
    get length() {
      return store.size;
    },
    clear: () => store.clear(),
    getItem: (key) => (store.has(key) ? store.get(key)! : null),
    key: (index) => [...store.keys()][index] ?? null,
    removeItem: (key) => {
      store.delete(key);
    },
    setItem: (key, value) => {
      store.set(key, String(value));
    },
  };

  for (const target of [window, globalThis]) {
    try {
      Object.defineProperty(target, 'localStorage', {
        value: shim,
        configurable: true,
        writable: true,
      });
    } catch {
      // 目标是冻结的就算了 —— jsdom 那份能用就不需要它
    }
  }
}

ensureLocalStorage();
