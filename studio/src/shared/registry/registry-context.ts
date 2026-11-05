import { createContext } from 'react';
import type { AppRegistry } from './types';

/**
 * 注册表用 context 下发，而不是让 widgets 直接 import `@/app/registry`：
 * 依赖方向保持 widgets → shared，装配权只留在 app 层。
 */
export const RegistryContext = createContext<AppRegistry | null>(null);
