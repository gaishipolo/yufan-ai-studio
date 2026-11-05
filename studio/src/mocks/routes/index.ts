import type { MockRoute } from '../kernel';
import { registerMockRoutes } from '../registry';

/* ------------------------------------------------------------------
 * Mock 文件自动发现
 *
 * 命中两类文件：
 *   src/mocks/routes/*.mock.ts     —— 骨架自带的公共 mock
 *   src/modules/<id>/mock.ts       —— 每个功能模块自己的 mock
 *
 * 新增功能只要放一个 mock.ts 导出 `routes`，这里不用改。
 * ------------------------------------------------------------------ */

interface MockFile {
  routes: MockRoute[];
}

const files = import.meta.glob<MockFile>(['./*.mock.ts', '../../modules/*/mock.ts'], {
  eager: true,
});

Object.keys(files)
  .sort()
  .forEach((path) => {
    const file = files[path];
    if (!file?.routes) {
      throw new Error(`[mock] ${path} 必须 export const routes: MockRoute[]`);
    }
    registerMockRoutes(file.routes);
  });
