import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Group } from '@/entities/group/model/types';
import type { Role } from '@/entities/role/model/types';
import type { User } from '@/entities/user/model/types';
import { UserFormModal } from './UserFormModal';

/* 取数层只留 mutate：这里验的是「表单拦不拦、拦完发什么」，不验网络 */
const mutations = vi.hoisted(() => ({ create: vi.fn(), update: vi.fn() }));

vi.mock('@/entities/user/api/user.queries', () => ({
  useCreateUser: () => ({ mutate: mutations.create, isPending: false }),
  useUpdateUser: () => ({ mutate: mutations.update, isPending: false }),
}));

const EXISTING: User = {
  id: 'u_2',
  username: 'editor',
  displayName: '小编',
  email: 'editor@example.com',
  avatarUrl: 'https://cdn.example.com/e.png',
  isActive: true,
  lastLoginAt: null,
  createdAt: '2026-01-01T00:00:00.000Z',
  roles: [{ id: 'r_editor', code: 'editor', name: '内容编辑', isSuperuser: false }],
};

const ROLES: Role[] = [
  {
    id: 'r_editor',
    code: 'editor',
    name: '内容编辑',
    description: '内容生产全量',
    isSuperuser: false,
    isBuiltin: true,
    sortOrder: 10,
    createdAt: '2026-01-01T00:00:00.000Z',
    permissions: [],
    userCount: 1,
    orphanedPermissions: [],
  },
  {
    id: 'r_admin',
    code: 'admin',
    name: '超级管理员',
    description: '拥有全部权限',
    isSuperuser: true,
    isBuiltin: true,
    sortOrder: 0,
    createdAt: '2026-01-01T00:00:00.000Z',
    permissions: [],
    userCount: 1,
    orphanedPermissions: [],
  },
];

const GROUPS: Group[] = [
  {
    id: 'g_content',
    name: '内容组',
    description: '模板与素材的编辑同学',
    createdAt: '2026-02-01T00:00:00.000Z',
    permissions: [],
    memberIds: [],
    memberCount: 0,
  },
  {
    id: 'g_ops',
    name: '运营组',
    createdAt: '2026-02-02T00:00:00.000Z',
    permissions: [],
    memberIds: [],
    memberCount: 0,
  },
];

const renderModal = (
  user?: User | null,
  options: { roles?: Role[]; groups?: Group[]; onCreateRole?: () => void; onCreateGroup?: () => void } = {},
) =>
  render(
    <UserFormModal
      open
      user={user}
      roles={options.roles ?? []}
      groups={options.groups ?? []}
      onCreateRole={options.onCreateRole}
      onCreateGroup={options.onCreateGroup}
      onClose={vi.fn()}
    />,
  );

const submit = async (user: ReturnType<typeof userEvent.setup>) => {
  await user.click(screen.getByRole('button', { name: /创建|保存/ }));
};

describe('UserFormModal', () => {
  beforeEach(() => {
    mutations.create.mockReset();
    mutations.update.mockReset();
  });

  describe('创建模式', () => {
    it('初始密码、分组、角色只在创建时出现，头像地址不出现（建号接口不接收）', () => {
      renderModal();

      expect(screen.getByLabelText(/初始密码/)).toBeInTheDocument();
      expect(screen.getByRole('button', { name: '分组' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: '角色' })).toBeInTheDocument();
      // 角色多选下拉收起时不铺开复选框长列表
      expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
      expect(screen.queryByLabelText(/头像地址/)).not.toBeInTheDocument();
    });

    it('用户名不合规时不发请求', async () => {
      const user = userEvent.setup();
      renderModal();

      await user.type(screen.getByLabelText(/登录名/), '中文名');
      await user.type(screen.getByLabelText(/初始密码/), 'alice12345');
      await submit(user);

      expect(screen.getByRole('alert')).toHaveTextContent('用户名 3-64 位');
      expect(mutations.create).not.toHaveBeenCalled();
    });

    it('纯数字口令被拦下（后端也会拦，这里是少一次往返）', async () => {
      const user = userEvent.setup();
      renderModal();

      await user.type(screen.getByLabelText(/登录名/), 'alice');
      await user.type(screen.getByLabelText(/初始密码/), '1234567890');
      await submit(user);

      expect(screen.getByRole('alert')).toHaveTextContent('初始密码不能是纯数字');
      expect(mutations.create).not.toHaveBeenCalled();
    });

    it('邮箱格式错误时不发请求', async () => {
      const user = userEvent.setup();
      renderModal();

      await user.type(screen.getByLabelText(/登录名/), 'alice');
      await user.type(screen.getByLabelText(/初始密码/), 'alice12345');
      /* 故意选「浏览器原生校验放过、我们的规则不放过」的地址：
         像 alice@ 这种连原生校验都过不了的输入，表单压根不会触发 submit，
         测到的是浏览器而不是我们这一层。 */
      await user.type(screen.getByLabelText(/邮箱/), 'alice@localhost');
      await submit(user);

      expect(screen.getByRole('alert')).toHaveTextContent('邮箱格式不正确');
      expect(mutations.create).not.toHaveBeenCalled();
    });

    it('全部合法时把登录名转小写后提交', async () => {
      const user = userEvent.setup();
      renderModal();

      await user.type(screen.getByLabelText(/登录名/), 'Alice');
      await user.type(screen.getByLabelText(/初始密码/), 'alice12345');
      await submit(user);

      expect(mutations.create).toHaveBeenCalledTimes(1);
      expect(mutations.create.mock.calls[0][0]).toMatchObject({
        username: 'alice',
        password: 'alice12345',
        email: null,
        groupIds: [],
        roleIds: [],
      });
    });

    it('分组与角色都可多选，提交时带上各自的 id', async () => {
      const user = userEvent.setup();
      renderModal(null, { roles: ROLES, groups: GROUPS });

      await user.type(screen.getByLabelText(/登录名/), 'Alice');
      await user.type(screen.getByLabelText(/初始密码/), 'alice12345');
      await user.click(screen.getByRole('button', { name: '分组' }));
      await user.click(screen.getByRole('checkbox', { name: /内容组/ }));
      await user.keyboard('{Escape}');
      await user.click(screen.getByRole('button', { name: '角色' }));
      await user.click(screen.getByRole('checkbox', { name: /内容编辑/ }));
      await user.click(screen.getByRole('checkbox', { name: /超级管理员/ }));
      // 多选收起态只显示短摘要，不把名字全铺出来
      expect(screen.getByRole('button', { name: '角色' })).toHaveTextContent('已选 2 项');
      await user.keyboard('{Escape}');

      await submit(user);

      expect(mutations.create).toHaveBeenCalledTimes(1);
      expect(mutations.create.mock.calls[0][0]).toMatchObject({
        username: 'alice',
        groupIds: ['g_content'],
        roleIds: ['r_editor', 'r_admin'],
      });
    });

    it('分组取消勾选会移除该项，角色取消勾选也会移除', async () => {
      const user = userEvent.setup();
      renderModal(null, { roles: ROLES, groups: GROUPS });

      await user.click(screen.getByRole('button', { name: '分组' }));
      await user.click(screen.getByRole('checkbox', { name: /内容组/ }));
      await user.click(screen.getByRole('checkbox', { name: /内容组/ }));
      await user.keyboard('{Escape}');
      await user.click(screen.getByRole('button', { name: '角色' }));
      await user.click(screen.getByRole('checkbox', { name: /内容编辑/ }));
      await user.click(screen.getByRole('checkbox', { name: /超级管理员/ }));
      await user.click(screen.getByRole('checkbox', { name: /内容编辑/ }));
      await user.keyboard('{Escape}');

      await user.type(screen.getByLabelText(/登录名/), 'alice');
      await user.type(screen.getByLabelText(/初始密码/), 'alice12345');
      await submit(user);

      expect(mutations.create.mock.calls[0][0]).toMatchObject({
        // 分组勾了又取消 → 提交空数组（不是残留上一次的值）
        groupIds: [],
        roleIds: ['r_admin'],
      });
    });

    it('没有分组时多选给出空态，不影响创建', async () => {
      const user = userEvent.setup();
      renderModal(null, { roles: ROLES });

      await user.click(screen.getByRole('button', { name: '分组' }));
      expect(screen.getByText('还没有可用分组')).toBeInTheDocument();
      await user.keyboard('{Escape}');

      await user.type(screen.getByLabelText(/登录名/), 'alice');
      await user.type(screen.getByLabelText(/初始密码/), 'alice12345');
      await submit(user);
      expect(mutations.create).toHaveBeenCalledTimes(1);
    });

    /* 空态必须是个**有出口**的空态：只有「还没有可用角色」这一句话，
       等于把用户支去别的页签自己找路 —— 叫用户走两步还得记得回来，
       那是死胡同。入口直接摆在空态里。 */
    describe('空态里的创建入口', () => {
      it('一个角色都没有时，空态里直接有「新建角色」按钮', async () => {
        const user = userEvent.setup();
        renderModal(null, { groups: GROUPS, onCreateRole: vi.fn() });

        await user.click(screen.getByRole('button', { name: '角色' }));

        expect(screen.getByText('还没有可用角色')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: '新建角色' })).toBeInTheDocument();
      });

      it('一个小组都没有时，空态里直接有「新增分组」按钮', async () => {
        const user = userEvent.setup();
        renderModal(null, { roles: ROLES, onCreateGroup: vi.fn() });

        await user.click(screen.getByRole('button', { name: '分组' }));

        expect(screen.getByText('还没有可用分组')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: '新增分组' })).toBeInTheDocument();
      });

      it('点了入口会回调创建动作', async () => {
        const user = userEvent.setup();
        const onCreateRole = vi.fn();
        renderModal(null, { groups: GROUPS, onCreateRole });

        await user.click(screen.getByRole('button', { name: '角色' }));
        await user.click(screen.getByRole('button', { name: '新建角色' }));

        expect(onCreateRole).toHaveBeenCalledTimes(1);
      });

      it('调用方没给入口时，空态只剩一句话（不摆死按钮）', async () => {
        const user = userEvent.setup();
        renderModal(null, { groups: GROUPS });

        await user.click(screen.getByRole('button', { name: '角色' }));

        expect(screen.getByText('还没有可用角色')).toBeInTheDocument();
        expect(screen.queryByRole('button', { name: '新建角色' })).not.toBeInTheDocument();
      });

      it('有角色时不出这个入口（入口只在真的没东西可选时出现）', async () => {
        const user = userEvent.setup();
        renderModal(null, { roles: ROLES, groups: GROUPS });

        await user.click(screen.getByRole('button', { name: '角色' }));

        expect(screen.queryByRole('button', { name: '新建角色' })).not.toBeInTheDocument();
        expect(screen.getByRole('checkbox', { name: /内容编辑/ })).toBeInTheDocument();
      });
    });
  });

  describe('编辑模式', () => {
    it('隐藏密码、分组与角色，回填头像地址', () => {
      renderModal(EXISTING, { roles: ROLES, groups: GROUPS });

      expect(screen.queryByLabelText(/初始密码/)).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: '角色' })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: '分组' })).not.toBeInTheDocument();
      expect(screen.getByLabelText(/头像地址/)).toHaveValue('https://cdn.example.com/e.png');
      expect(screen.getByLabelText(/登录名/)).toBeDisabled();
    });

    it('保存时把 avatarUrl 一起提交（后端 PATCH 支持该字段）', async () => {
      const user = userEvent.setup();
      renderModal(EXISTING);

      const avatar = screen.getByLabelText(/头像地址/);
      await user.clear(avatar);
      await user.type(avatar, 'https://cdn.example.com/new.png');
      await submit(user);

      expect(mutations.update).toHaveBeenCalledTimes(1);
      expect(mutations.update.mock.calls[0][0]).toMatchObject({
        id: 'u_2',
        payload: {
          displayName: '小编',
          email: 'editor@example.com',
          avatarUrl: 'https://cdn.example.com/new.png',
        },
      });
    });

    it('清空头像地址时提交 null，而不是空串（后端 null 才是「取消头像」）', async () => {
      const user = userEvent.setup();
      renderModal(EXISTING);

      await user.clear(screen.getByLabelText(/头像地址/));
      await submit(user);

      expect(mutations.update.mock.calls[0][0].payload.avatarUrl).toBeNull();
    });

    it('非法头像地址被拦下', async () => {
      const user = userEvent.setup();
      renderModal(EXISTING);

      const avatar = screen.getByLabelText(/头像地址/);
      await user.clear(avatar);
      await user.type(avatar, 'javascript:alert(1)');
      await submit(user);

      expect(screen.getByRole('alert')).toHaveTextContent('头像地址需以 http(s):// 或 / 开头');
      expect(mutations.update).not.toHaveBeenCalled();
    });
  });
});
