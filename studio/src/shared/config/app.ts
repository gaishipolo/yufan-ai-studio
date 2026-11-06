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

/* ---------------- 品牌文案（唯一来源）----------------
 * 顶栏、登录页、浏览器标签页、站点描述全部从这里读。
 * `index.html` 是静态 HTML，没法 import TS，所以那里保留同样的字面量，
 * 由 `app.test.ts` 做字节级比对钉住——改品牌只需改本文件 + index.html，
 * 漏改任何一处测试都会红。favicon.svg 是纯图形，不含文字，无需同步。
 */

/** 品牌字标：顶栏与登录页共用的两个汉字 */
export const APP_BRAND = '雨范';

/** 字标下方的英文副标 */
export const APP_BRAND_EN = 'AI STUDIO';

/** 版本号：顶栏徽标与登录页版权行共用 */
export const APP_VERSION = 'v3.0';

/** 产品名（顶栏与登录页的主标题） */
export const APP_NAME = 'AI 短视频工作台';

/** 完整产品名：浏览器标签页 `<title>` 用它 */
export const APP_FULL_NAME = `${APP_BRAND} ${APP_NAME}`;

/** 站点描述：`index.html` 的 meta description 必须与之一致 */
export const APP_DESCRIPTION = `${APP_FULL_NAME} — 生成、任务、素材、模板一体化`;

/**
 * 接口前缀。唯一的来源：主通道（`shared/api/http`）与认证旁路通道
 * （`shared/api/auth`）必须落在同一个前缀上，否则续期会打到别的地址。
 */
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api/v1';
