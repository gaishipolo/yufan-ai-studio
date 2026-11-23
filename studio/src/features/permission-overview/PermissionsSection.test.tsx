import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const treeMock = vi.hoisted(() => ({ data: undefined as unknown, isLoading: false, isError: false, error: null, refetch: vi.fn() }));

vi.mock('@/entities/user/api/user.queries', () => ({
  usePermissionTree: () => treeMock,
}));

/* 右栏是另一套取数（详情 + 有效权限 + 目录），这里只关心「选中谁」，
   所以替换成一个能读出 userId 的占位块。 */
vi.mock('./UserPermissionsPanel', () => ({
  UserPermissionsPanel: ({ userId }: { userId: string }) => <div>面板:{userId}</div>,
}));

import { PermissionsSection } from './PermissionsSection';

const TREE = {
  groups: [
    {
      id: 'g1',
      name: '设计组',
      members: [
        { id: 'u1', username: 'alice', displayName: '爱丽丝', isActive: true },
        { id: 'u2', username: 'bob', displayName: '鲍勃', isActive: true },
      ],
    },
    {
      id: 'g2',
      name: '运营组',
      members: [{ id: 'u3', username: 'carol', displayName: '卡罗尔', isActive: false }],
    },
  ],
  ungrouped: [{ id: 'u4', username: 'dave', displayName: '戴夫', isActive: true }],
};

describe('PermissionsSection：左树选人，右侧看权限', () => {
  beforeEach(() => {
    treeMock.data = TREE;
    treeMock.isLoading = false;
    treeMock.isError = false;
  });

  it('先给出空态，而不是自作主张选一个人', async () => {
    render(<PermissionsSection />);
    expect(screen.getByText('从左边选一个账号')).toBeInTheDocument();
    expect(screen.queryByText(/面板:/)).toBeNull();
  });

  it('分组带成员数，未分组单列', () => {
    render(<PermissionsSection />);

    expect(screen.getByText('设计组')).toBeInTheDocument();
    expect(screen.getByText('运营组')).toBeInTheDocument();
    expect(screen.getByText('未分组')).toBeInTheDocument();
    // 未分组的人只出现在「未分组」下
    expect(screen.getByText('戴夫')).toBeInTheDocument();
  });

  it('点成员 → 右侧渲染这个人的面板', async () => {
    const user = userEvent.setup();
    render(<PermissionsSection />);

    await user.click(screen.getByText('爱丽丝'));
    expect(screen.getByText('面板:u1')).toBeInTheDocument();

    await user.click(screen.getByText('鲍勃'));
    expect(screen.getByText('面板:u2')).toBeInTheDocument();
  });

  it('折叠分组隐藏成员，但组名与计数还在', async () => {
    const user = userEvent.setup();
    render(<PermissionsSection />);

    await user.click(screen.getByRole('button', { name: /设计组/ }));

    expect(screen.queryByText('爱丽丝')).toBeNull();
    expect(screen.getByText('设计组')).toBeInTheDocument();
  });

  it('未分组也能折叠（它和真实分组共用一份开合状态）', async () => {
    const user = userEvent.setup();
    render(<PermissionsSection />);

    expect(screen.getByText('戴夫')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /未分组/ }));

    expect(screen.queryByText('戴夫')).toBeNull();
    // 折叠的是成员，标题与人数还在
    expect(screen.getByText('未分组')).toBeInTheDocument();
  });

  it('搜索按账号或昵称过滤，且命中的组自动展开', async () => {
    const user = userEvent.setup();
    render(<PermissionsSection />);

    await user.type(screen.getByLabelText('搜索账号'), 'carol');

    expect(screen.getByText('卡罗尔')).toBeInTheDocument();
    // 没命中的组整个消失（否则要在折叠的树里一格格翻）
    expect(screen.queryByText('设计组')).toBeNull();
  });

  it('停用账号仍然列出，标「已停用」—— 他可能还挂着授权', () => {
    render(<PermissionsSection />);

    const row = screen.getByText('卡罗尔').closest('button') as HTMLElement;
    expect(within(row).getByText('已停用')).toBeInTheDocument();
  });

  it('搜不到时给一句明确的话', async () => {
    const user = userEvent.setup();
    render(<PermissionsSection />);

    await user.type(screen.getByLabelText('搜索账号'), 'zzz不存在');
    expect(screen.getByText('没有匹配的账号')).toBeInTheDocument();
  });
});
