import type { AxiosAdapter } from 'axios';

/* ------------------------------------------------------------------
 * mock 适配器的按需加载
 *
 * 必须是**动态** import，而且只能从「构建期会消失的分支」里调用：
 * `VITE_USE_MOCK` 在构建期被替换成字面量，生产构建里 `USE_MOCK` 恒为 false，
 * 那条 `if` 会被整块剔除，`loadMockAdapter` 于是成了无人引用的模块，
 * rollup 连里面的动态 import 一起删掉 —— mock 数据与演示账号口令都不会进产物。
 *
 * 抽成独立文件是因为**两个** axios 实例需要它：
 * 主干（`http`，业务请求）与认证旁路（`auth`，续期 / 登出）。
 * 各自写一份 `??= import(...)` 容易出现「一处接了 mock、另一处漏了」，
 * 表现是 mock 模式下跑到 2 小时就再也续不上。
 * ------------------------------------------------------------------ */

/** 构建期常量：production 恒为 false */
export const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true';

let adapterPromise: Promise<AxiosAdapter> | null = null;

/** 拿到 mock 适配器（同一个 Promise，模块只加载一次） */
export function loadMockAdapter(): Promise<AxiosAdapter> {
  adapterPromise ??= import('@/mocks/adapter').then(({ mockAdapter }) => mockAdapter);
  return adapterPromise;
}
