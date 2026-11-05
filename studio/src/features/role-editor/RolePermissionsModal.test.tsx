import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Role } from '@/entities/role/model/types';
import { RolePermissionsModal } from './RolePermissionsModal';

/* 目录必须是**同一个对象引用**：组件用 useMemo(data) 算「有效权限码」，
   每次渲染返回新对象会让 effect 反复 setSelected，测试直接卡死。 */
const h = vi.hoisted(() => ({
  save: vi.fn(),
  CATALOG: {
    groups: [
      {
        group: '内容生产',
        items: [
          { code: 'task:read', name: '查看任务', description: '' },
          { code: 'task:create', name: '创建任务', description: '' },
        ],
      },
      {
        group: '组织管理',
        items: [{ code: 'identity:read', name: '查看用户', description: '' }],
      },
    ],
  },
}));

vi.mock('@/entities/role/api/role.queries', () => ({
  usePermissionCatalog: () => ({
    data: h.CATALOG,
    isLoading: false,
    isError: false,
    error: null,
  }),
  useSetRolePermissions: () => ({ mutate: h.save, isPending: false }),
}));

const baseRole = (patch: Partial<Role> = {}): Role => ({
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
  ...patch,
});

const renderModal = (role: Role) =>
  render(<RolePermissionsModal open role={role} onClose={vi.fn()} />);

describe('RolePermissionsModal', () => {
  beforeEach(() => {
    h.save.mockReset();
  });

  it('超管角色不提供保存，只说明权限恒为全部', () => {
    renderModal(baseRole({ isSuperuser: true, permissions: ['task:read'] }));

    expect(screen.getByText(/权限恒为全部/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '保存' })).not.toBeInTheDocument();
    // 弹窗头部还有一个 aria-label="关闭" 的 X 按钮，所以只能断言「至少存在一个」
    expect(screen.getAllByRole('button', { name: '关闭' }).length).toBeGreaterThan(0);
  });

  it('已下线的权限码会被提示，并且保存时自动剔除', async () => {
    const user = userEvent.setup();
    renderModal(
      baseRole({ permissions: ['task:read', 'legacy:gone'], orphanedPermissions: ['legacy:gone'] }),
    );

    expect(screen.getByText(/含 1 个已失效的权限码/)).toBeInTheDocument();
    expect(screen.getByText('legacy:gone')).toBeInTheDocument();

    await waitFor(() => expect(screen.getByRole('checkbox', { name: /查看任务/ })).toBeChecked());
    await user.click(screen.getByRole('button', { name: '保存' }));

    expect(h.save).toHaveBeenCalledWith(
      { id: 'r_1', permissions: ['task:read'] },
      expect.anything(),
    );
  });

  it('勾选与取消后的集合原样提交（整组替换语义）', async () => {
    const user = userEvent.setup();
    renderModal(baseRole({ permissions: ['task:read'] }));

    await waitFor(() => expect(screen.getByRole('checkbox', { name: /查看任务/ })).toBeChecked());
    await user.click(screen.getByRole('checkbox', { name: /创建任务/ }));
    await user.click(screen.getByRole('button', { name: '保存' }));

    expect(h.save.mock.calls[0][0]).toEqual({
      id: 'r_1',
      permissions: ['task:read', 'task:create'],
    });
  });

  it('「全部清空」把已选清成空数组', async () => {
    const user = userEvent.setup();
    renderModal(baseRole({ permissions: ['task:read', 'identity:read'] }));

    /* 用勾选状态判断，而不是抓「已选 2 个」那串数字：
       分组标题旁边也有数字，按文本取会撞在一起 */
    const checkedCount = () =>
      screen.getAllByRole('checkbox').filter((box) => (box as HTMLInputElement).checked).length;

    await waitFor(() => expect(checkedCount()).toBe(2));
    await user.click(screen.getByRole('button', { name: '全部清空' }));

    expect(checkedCount()).toBe(0);
  });

  it('整组全选只在未全选时出现', async () => {
    const user = userEvent.setup();
    renderModal(baseRole());

    /* 弹窗是 portal 到 body 的，render 返回的 container 里并没有它的 DOM；
       两个分组各有一个「全选本组」，必须限定在第一组里操作 */
    const firstGroup = document.body.querySelector('section');
    if (!firstGroup) throw new Error('权限分组未渲染');
    await user.click(within(firstGroup).getByRole('button', { name: '全选本组' }));

    expect(screen.getAllByRole('button', { name: '取消本组' })).toHaveLength(1);
  });
});
