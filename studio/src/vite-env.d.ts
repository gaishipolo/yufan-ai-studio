/// <reference types="vite/client" />

interface ImportMetaEnv {
  /* 产品名**不在这里**：`VITE_APP_TITLE` 曾经存在但全仓无人读取，
     真正生效的是 shared/config/app.ts 的 APP_NAME（界面）与 index.html 的 <title>（标签页）。
     留着两个"看起来能改名字"的开关，改名时只会改错那一个。 */
  readonly VITE_API_BASE_URL: string;
  readonly VITE_USE_MOCK: string;
  readonly VITE_API_PROXY_TARGET?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
