import type { ReactNode } from 'react';
import { RegistryContext } from './registry-context';
import type { AppRegistry } from './types';

export function RegistryProvider({
  registry,
  children,
}: {
  registry: AppRegistry;
  children: ReactNode;
}) {
  return <RegistryContext.Provider value={registry}>{children}</RegistryContext.Provider>;
}
