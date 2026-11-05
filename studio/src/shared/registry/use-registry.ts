import { useContext } from 'react';
import { RegistryContext } from './registry-context';
import type { AppRegistry } from './types';

/** 必须在 <RegistryProvider> 内使用；缺了就直接报错，避免静默渲染空导航 */
export function useRegistry(): AppRegistry {
  const registry = useContext(RegistryContext);
  if (!registry) {
    throw new Error('[registry] 缺少 <RegistryProvider>：请确认 AppProviders 已包裹应用根节点');
  }
  return registry;
}

/** 允许缺省（用于可独立渲染的插槽组件） */
export function useOptionalRegistry(): AppRegistry | null {
  return useContext(RegistryContext);
}
