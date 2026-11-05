import { createRegistry } from '@/shared/registry';
import type { AppModule } from '@/shared/registry';

/* ------------------------------------------------------------------
 * 模块自动发现 —— 骨架里唯一需要「知道有哪些功能」的地方
 *
 * `import.meta.glob` 在构建期展开：新增 src/modules/<id>/module.ts
 * 会被自动收集，无需在路由表 / 导航里登记（也就没有多人改同一行的冲突）。
 * eager 只加载「清单」本身（几十字节），页面组件仍由清单里的
 * `load: () => import(...)` 按路由分包加载。
 * ------------------------------------------------------------------ */

const discovered = import.meta.glob<{ default: AppModule }>('../../modules/*/module.ts', {
  eager: true,
});

const modules = Object.keys(discovered)
  .sort()
  .map((path) => {
    const mod = discovered[path]?.default;
    if (!mod) {
      throw new Error(`[registry] ${path} 必须 default export defineModule({ ... })`);
    }
    return mod;
  });

export const appRegistry = createRegistry(modules);
