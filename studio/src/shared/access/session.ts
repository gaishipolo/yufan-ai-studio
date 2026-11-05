import { create } from 'zustand';
import { GUEST_SUBJECT, type Subject } from './types';

/* ------------------------------------------------------------------
 * 会话（身份）状态 —— 骨架里唯一的「当前用户」出口
 *
 * 登录模块 / 权限管理模块只调用 setSubject / signOut，
 * 导航、路由守卫、<Can> 会自动跟着变，不需要改任何骨架代码。
 * ------------------------------------------------------------------ */

/**
 * 本地开发用身份：没有登录功能时，用环境变量预置权限点即可调试受保护的页面。
 * 只有 DEV 构建才会读取，生产构建里这段会被摇掉。
 *
 * @example .env.development
 * VITE_DEV_PERMISSIONS=task:read,user:manage
 */
function resolveInitialSubject(): Subject {
  const raw = import.meta.env.DEV ? import.meta.env.VITE_DEV_PERMISSIONS : undefined;
  const permissions = (raw ?? '')
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
  if (!permissions.length) return GUEST_SUBJECT;
  return {
    id: 'dev',
    name: '本地开发账号',
    roles: ['dev'],
    permissions,
  };
}

export type SessionStatus = 'anonymous' | 'authenticated';

interface SessionState {
  status: SessionStatus;
  subject: Subject;
  /** 登录成功 / 刷新用户信息后写入 */
  setSubject: (subject: Subject) => void;
  /** 退出登录 / 401 时清空 */
  signOut: () => void;
}

const INITIAL_SUBJECT = resolveInitialSubject();

export const useSessionStore = create<SessionState>()((set) => ({
  status: INITIAL_SUBJECT.id === 'anonymous' ? 'anonymous' : 'authenticated',
  subject: INITIAL_SUBJECT,
  setSubject: (subject) =>
    set({ subject, status: subject.id === 'anonymous' ? 'anonymous' : 'authenticated' }),
  signOut: () => set({ subject: GUEST_SUBJECT, status: 'anonymous' }),
}));
