import type { ReactNode } from 'react';
import { AccessContext } from './access-context';
import type { Subject } from './types';

/**
 * 身份注入点。骨架里由 AppProviders 传入 session store 的 subject；
 * 测试里可以直接传一个 subject，无需 mock 全局状态。
 */
export function AccessProvider({ subject, children }: { subject: Subject; children: ReactNode }) {
  return <AccessContext.Provider value={subject}>{children}</AccessContext.Provider>;
}
