import type { AppModule } from './types';

/**
 * 模块声明入口。只做类型收窄与恒等返回：
 * - 让 IDE 在写 module.ts 时就能提示缺字段 / 拼错字段
 * - 给未来的构建期校验（比如加 `schema` 字段）留一个统一入口
 *
 * @example
 * export default defineModule({
 *   id: 'user-access',
 *   routes: [{ path: 'system/users', load: () => import('@/pages/user-access/UserAccessPage') }],
 * });
 */
export function defineModule(module: AppModule): AppModule {
  return module;
}
