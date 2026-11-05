/**
 * 应用级常量（骨架配置，改动需评审）。
 * 放在 shared 是因为路由（app）、顶栏（widgets）、404（pages）都要读 —— 下沉才能保持单向依赖。
 *
 * 业务常量不要往这里加：可扩展项一律在 src/modules/<id>/module.ts 里声明。
 */

/** 默认首页：访问 `/` 或点击品牌名时跳转 */
export const HOME_PATH = '/tasks';

/**
 * 登录页路径。
 * 登录页是 `layout: 'bare'` 的独立页面（不套外壳），路由由 auth 模块声明；
 * 守卫（RequireAuth）与顶栏登出要跳这里，所以路径本身必须是骨架常量，
 * 避免两处各写一遍导致「守卫跳到不存在的页面」。
 */
export const LOGIN_PATH = '/login';

/** 顶栏产品名 */
export const APP_NAME = 'AI 短视频工作台';

/**
 * 接口前缀。唯一的来源：主通道（`shared/api/http`）与认证旁路通道
 * （`shared/api/auth`）必须落在同一个前缀上，否则续期会打到别的地址。
 */
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api/v1';
