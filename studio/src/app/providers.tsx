import type { ReactNode } from 'react';
import { QueryClientProvider } from '@tanstack/react-query';
import { AccessProvider } from '@/shared/access/AccessProvider';
import { useSessionStore } from '@/shared/access/session';
import { queryClient } from '@/shared/api/query-client';
import { RegistryProvider } from '@/shared/registry/RegistryProvider';
import { appRegistry } from './registry';

/**
 * 全局 Provider 组合。
 * 身份来自 session store（登录模块写入），注册表来自自动发现。
 * 新增功能不需要动这里；只有引入「全局横向能力」（主题 / i18n / 埋点）时才加一层。
 */
export function AppProviders({ children }: { children: ReactNode }) {
  const subject = useSessionStore((state) => state.subject);

  return (
    <QueryClientProvider client={queryClient}>
      <AccessProvider subject={subject}>
        <RegistryProvider registry={appRegistry}>{children}</RegistryProvider>
      </AccessProvider>
    </QueryClientProvider>
  );
}
