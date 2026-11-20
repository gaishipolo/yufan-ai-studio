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
       权限勾选器现在是「页面段 + 接口段」两段，接口段里每个资源分组各有
       一个「全选本组」——必须限定到具体那个分组，否则会点到两个同名按钮 */
    const groupHeading = screen.getByRole('heading', { name: '内容生产' });
    const group = groupHeading.closest('div');
    if (!group) throw new Error('权限分组未渲染');
    await user.click(within(group).getByRole('button', { name: '全选本组' }));

    expect(screen.getAllByRole('button', { name: '取消本组' })).toHaveLength(1);
  });
});

/* 回归：`task:*` / `image:*` 不在权限目录里（目录只有具体码），但后端明确接受。
   以前这里用「目录里有」当判据，于是打开内置「内容编辑」再点保存，就把这两条
   通配静默删掉了 —— 它们覆盖十几个接口，界面上的表现只是「已选少了几项」。 */
describe('RolePermissionsModal · 整类授权（通配）', () => {
  beforeEach(() => {
    h.save.mockReset();
  });

  it('通配留在选中集合里，保存时原样提交', async () => {
    const user = userEvent.setup();
    renderModal(
      baseRole({ permissions: ['task:read', 'task:*'], orphanedPermissions: [] }),
    );

    // 通配不计入「目录里没有」的失效项，所以不该出现「保存后自动清除」那句
    expect(screen.queryByText(/已下线/)).not.toBeInTheDocument();
    // 它现在有一行可点（PermissionPicker 的「目录外」段）
    expect(await screen.findByRole('checkbox', { name: 'task:* 接口权限' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => expect(h.save).toHaveBeenCalled());
    const sent = h.save.mock.calls[0][0] as { permissions: string[] };
    expect(sent.permissions).toContain('task:*');
    expect(sent.permissions).toContain('task:read');
  });

  it('真正失效的码（后端也不认）仍然被排除，还是那句「保存后自动清除」', async () => {
    const user = userEvent.setup();
    renderModal(
      baseRole({ permissions: ['task:read', 'asset:read'], orphanedPermissions: ['asset:read'] }),
    );

    expect(screen.getByText(/已下线/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => expect(h.save).toHaveBeenCalled());
    const sent = h.save.mock.calls[0][0] as { permissions: string[] };
    expect(sent.permissions).toEqual(['task:read']);
  });
});
