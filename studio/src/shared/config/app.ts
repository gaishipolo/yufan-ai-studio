/**
 * 应用级常量（骨架配置，改动需评审）。
 * 放在 shared 是因为路由（app）、顶栏（widgets）、404（pages）都要读 —— 下沉才能保持单向依赖。
 *
 * 业务常量不要往这里加：可扩展项一律在 src/modules/<id>/module.ts 里声明。
 */

/** 默认首页：访问 `/` 或点击品牌名时跳转 */
export const HOME_PATH = '/tasks';

/** 顶栏产品名 */
export const APP_NAME = 'AI 短视频工作台';
