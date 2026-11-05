/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_APP_TITLE: string;
  readonly VITE_API_BASE_URL: string;
  readonly VITE_USE_MOCK: string;
  readonly VITE_API_PROXY_TARGET?: string;
  /** 仅 DEV：用逗号分隔的权限点伪造本地身份，便于调试受保护页面 */
  readonly VITE_DEV_PERMISSIONS?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
