import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { AccessProvider } from '@/shared/access/AccessProvider';
import type { Subject } from '@/shared/access/types';
import type { User } from '@/entities/user/model/types';
import type { Role } from '@/entities/role/model/types';
import { AssignRolesModal } from '@/features/user-roles/AssignRolesModal';
import { ResetPasswordModal } from '@/features/user-security/ResetPasswordModal';
import { DeactivateUserModal } from '@/features/user-security/DeactivateUserModal';
import { ChangePasswordModal } from '@/features/auth/ChangePasswordModal';
import { PermissionPicker } from './PermissionPicker';

/**
 * 「去掉说明性小字」这一轮的守卫（2026-11-18 用户拍板）。
 *
 * 判据：**解释**能并进控件就别单独摆一行。
 * 优先级：占位符 > 选项名 > `title` > 常驻 `<p>` / `hint=`。
 *
 * 但**约束**要留 —— 删掉的是「解释」，不是「信息」：
 * 「保存后整组替换」「改密后会被登出」这类用户不看输入框猜不到的东西，
 * 一旦删掉管理员就会做出错误预期（以为取消勾选只是「这次不勾」、
 * 以为改完密码还能继续用）。所以这份守卫分成两类：
 *
 * 1. 「不该有的说明」—— 必须**不在**版面上；
 * 2. 「该有的约束」—— 必须**在**（且只在一处）。
 *
 * 只钉前者会漏掉「顺手把约束也删了」，只钉后者则钉不住本轮的核心动作。
 */

vi.mock('@/entities/user/api/user.queries', () => ({
  useSetUserRoles: () => ({ mutate: vi.fn(), isPending: false }),
  useResetPassword: () => ({ mutate: vi.fn(), isPending: false }),
  useDeactivateUser: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock('@/entities/session/api/session.queries', () => ({
  useChangePassword: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock('@/entities/role/api/role.queries', () => ({
  usePermissionCatalog: () => ({
    data: {
      groups: [
        {
          group: '组织管理',
          items: [{ code: 'identity:read', name: '查看用户', description: '' }],
        },
      ],
    },
    isLoading: false,
    isError: false,
    error: null,
  }),
}));

const SUBJECT: Subject = { id: 'u_other', name: '老大', roles: ['admin'], permissions: ['*'] };

const ROLE: Role = {
  id: 'r_1',
  code: 'editor',
  name: '内容编辑',
  description: '',
  isSuperuser: false,
  isBuiltin: false,
  sortOrder: 10,
  createdAt: '2026-01-01T00:00:00.000Z',
  permissions: [],
  userCount: 0,
  orphanedPermissions: [],
};

const USER: User = {
  id: 'u_1',
  username: 'alice',
  displayName: 'Alice',
  isActive: true,
  lastLoginAt: null,
  createdAt: '2026-01-01T00:00:00.000Z',
  roles: [],
};

/** 这些弹窗都要 QueryClient（mutations）与 AccessProvider（当前登录者） */
function wrap(node: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <AccessProvider subject={SUBJECT}>{node}</AccessProvider>
    </QueryClientProvider>,
  );
}

describe('权限/登录/表单 · 不该有的说明性小字已经不在版面上', () => {
  it('分配角色：不再说「提交后整组替换」（那是权限勾选器的约束，不是这里的事）', () => {
    wrap(<AssignRolesModal open user={USER} roles={[ROLE]} onClose={vi.fn()} />);
    expect(screen.queryByText(/整组替换/)).not.toBeInTheDocument();
    // 但「什么时候生效」这句留下来了 —— 只有这一处能说清
    expect(screen.getByText(/权限变更在用户下次请求时生效/)).toBeInTheDocument();
  });

  it('停用用户：不再说「之后可在列表里重新启用」（停用这个词自带这个意思）', () => {
    wrap(<DeactivateUserModal open user={USER} onClose={vi.fn()} />);
    expect(screen.queryByText(/重新启用/)).not.toBeInTheDocument();
    // 但「会被后端拒绝」的边界留下来了 —— 那是猜不到的
    expect(screen.getByText(/停用自己或停用最后一个超管会被后端拒绝/)).toBeInTheDocument();
  });

  it('重置密码：不再重复「含正在使用中的会话」（前面那句已经说了全部下线）', () => {
    wrap(<ResetPasswordModal open user={USER} onClose={vi.fn()} />);
    expect(screen.queryByText(/含正在使用中的会话/)).not.toBeInTheDocument();
    expect(screen.getByText(/所有已登录设备会被强制下线/)).toBeInTheDocument();
  });

  it('权限勾选器：「保存后整组替换」只出现在共享摘要条这一处', () => {
    wrap(<PermissionPicker selected={[]} onChange={vi.fn()} />);
    // 约束不能删（取消勾选就真的不再授予），但也不能手抄多份：
    // 收进 PermissionSummaryBar，措辞只有一份。
    expect(screen.getAllByText(/保存后整组替换/)).toHaveLength(1);
  });

  it('权限勾选器：禁用的码不再配一条常驻警告框（理由在每个禁用项的 title 里）', () => {
    wrap(
      <PermissionPicker
        selected={[]}
        onChange={vi.fn()}
        forbidden={(code) => code === 'identity:read'}
        forbiddenHint="团队层不能授予该权限"
      />,
    );
    // 常驻警告框不见了，禁用项自己带着 title
    expect(screen.queryByText('团队层不能授予该权限')).not.toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: /查看用户/ })).toBeDisabled();
  });
});

describe('权限/登录/表单 · 该有的约束没有跟着被删掉', () => {
  it('改密码必须提前说「会退出所有设备」—— 后端 revoke_all 是硬事实', () => {
    wrap(<ChangePasswordModal open onClose={vi.fn()} />);
    expect(screen.getByText(/退出所有设备的登录/)).toBeInTheDocument();
  });

  it('口令约束提到「不同字符数」—— 后端第三条规则以前三个弹窗都没写', () => {
    wrap(<ChangePasswordModal open onClose={vi.fn()} />);
    expect(screen.getByText(/含 4 个不同字符/)).toBeInTheDocument();
  });
});
