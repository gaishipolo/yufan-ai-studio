import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { PermissionPicker } from './PermissionPicker';

/* 目录里必须同时有「页面」和「接口」，并且页面项带 `requires` ——
   层级关系（谁管着谁）就是从这两个字段来的，少一个这层就画不出来。 */
const h = vi.hoisted(() => ({
  CATALOG: {
    groups: [
      {
        group: '页面',
        items: [
          {
            code: 'page:task-center',
            name: '任务中心',
            description: '关联接口：task:read, task:create',
            requires: ['task:read', 'task:create'],
            route: '/tasks',
          },
          {
            code: 'page:library',
            name: '资产库',
            description: '关联接口：library:read',
            requires: ['library:read'],
            route: '/library',
          },
        ],
      },
      {
        group: '内容生产',
        items: [
          { code: 'task:read', name: '查看任务', description: '查看任务列表与任务详情' },
          { code: 'task:create', name: '创建任务', description: '' },
          { code: 'library:read', name: '查看资产库', description: '' },
          { code: 'export:read', name: '查看导出', description: '' },
        ],
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
}));

function renderPicker(selected: string[]) {
  const onChange = vi.fn();
  const view = render(<PermissionPicker selected={selected} onChange={onChange} />);
  return { onChange, ...view };
}

describe('PermissionPicker 的两层结构', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('页面与接口分成两段，页面卡片列出它关联的接口', () => {
    renderPicker([]);

    expect(screen.getByText(/页面权限 · 决定能看见哪一页/)).toBeInTheDocument();
    expect(screen.getByText(/接口权限 · 决定能做什么/)).toBeInTheDocument();

    // 页面条目上直接写着关联接口，不用去别处找
    const card = screen.getByRole('checkbox', { name: '任务中心 页面权限' }).closest('li');
    expect(card).not.toBeNull();
    expect(within(card as HTMLElement).getByText(/关联 2 个接口：查看任务、创建任务/)).toBeInTheDocument();
  });

  it('接口标出它被哪些页面管着', () => {
    renderPicker(['page:task-center']);

    const item = screen.getByRole('checkbox', { name: '查看任务 接口权限' }).closest('li');
    expect(item).not.toBeNull();
    expect(within(item as HTMLElement).getByText(/归 任务中心 管/)).toBeInTheDocument();
    expect(within(item as HTMLElement).getByText(/已生效/)).toBeInTheDocument();
  });

  it('页面没开的时候，接口标成「不生效」——勾之前就能看见', () => {
    renderPicker(['task:read']);

    const item = screen.getByRole('checkbox', { name: '查看任务 接口权限' }).closest('li');
    expect(item).not.toBeNull();
    expect(within(item as HTMLElement).getByText(/归 任务中心 管/)).toBeInTheDocument();
    expect(within(item as HTMLElement).getByText(/当前未生效/)).toBeInTheDocument();
  });

  it('没有任何页面声明的接口标成「不受页面开关影响」', () => {
    renderPicker([]);

    const item = screen.getByRole('checkbox', { name: '查看导出 接口权限' }).closest('li');
    expect(item).not.toBeNull();
    expect(within(item as HTMLElement).getByText('不受页面开关影响')).toBeInTheDocument();
  });

  it('摘要条分开报页面与接口的个数', () => {
    renderPicker(['page:task-center', 'task:read']);

    // 「页面 1」「接口 1」各自是一个独立计数，合成一个总数就看不出层级
    const page = screen.getByText('页面').parentElement;
    const api = screen.getByText('接口').parentElement;
    expect(page?.textContent).toContain('1');
    expect(api?.textContent).toContain('1');
  });

  it('别的层已授予的权限标成「已有 · 来源」，并说明它不在本层', () => {
    render(
      <PermissionPicker
        selected={[]}
        onChange={vi.fn()}
        inherited={{ 'task:read': ['角色'], 'page:task-center': ['角色'] }}
      />,
    );

    // 页面条目
    const page = screen.getByRole('checkbox', { name: '任务中心 页面权限' }).closest('li');
    expect(within(page as HTMLElement).getByText('已有 · 角色')).toBeInTheDocument();

    // 接口条目：来源写清楚「本层没有授予」
    const item = screen.getByRole('checkbox', { name: '查看任务 接口权限' }).closest('li');
    expect(within(item as HTMLElement).getByText('已有 · 角色')).toBeInTheDocument();
    expect(
      within(item as HTMLElement).getByText(/由 角色 授予，本层没有授予/),
    ).toBeInTheDocument();
  });

  it('勾选页面不会连带勾上接口（页面是可见性，接口是能力）', async () => {
    const user = userEvent.setup();
    const { onChange } = renderPicker([]);

    await user.click(screen.getByRole('checkbox', { name: '任务中心 页面权限' }));

    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange.mock.calls[0][0]).toEqual(['page:task-center']);
  });

  it('禁用的码点不动（这一层不能授的码）', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <PermissionPicker
        selected={[]}
        onChange={onChange}
        forbidden={(code) => code === 'page:library'}
        forbiddenHint="团队层不能授予该权限"
      />,
    );

    const checkbox = screen.getByRole('checkbox', { name: '资产库 页面权限' });
    expect(checkbox).toBeDisabled();
    await user.click(checkbox);
    expect(onChange).not.toHaveBeenCalled();
  });

  it('页面一个都不能授时，左栏整列不显示（全是禁用项的那一列没有信息量）', () => {
    render(
      <PermissionPicker
        selected={[]}
        onChange={vi.fn()}
        forbidden={(code) => code.startsWith('page:')}
        forbiddenHint="团队层不能授予该权限"
      />,
    );

    expect(screen.queryByText(/页面权限 · 决定能看见哪一页/)).not.toBeInTheDocument();
    expect(screen.getByText(/接口权限 · 决定能做什么/)).toBeInTheDocument();
  });

  it('页面名完整显示，不被截断（曾经「模板封装」被截成「模…」）', () => {
    renderPicker([]);

    const name = screen.getByRole('checkbox', { name: '任务中心 页面权限' })
      .closest('li')
      ?.querySelector('span.font-medium');

    expect(name?.textContent).toContain('任务中心');
    // 截断是 CSS 层面的（truncate → text-overflow + ellipsis），
    // 断言类名比断言像素更可靠：像素要布局引擎，类名是意图。
    expect(name?.className).not.toMatch(/truncate/);
  });

  it('页面名与权限码分行：码和路由不再挤占名称的宽度', () => {
    renderPicker([]);

    const item = screen.getByRole('checkbox', { name: '任务中心 页面权限' }).closest('li');
    // 权限码与路由在名称之外的一行里
    expect(within(item as HTMLElement).getByText('page:task-center')).toBeInTheDocument();
    expect(within(item as HTMLElement).getByText('/tasks')).toBeInTheDocument();
  });

  it('接口说明最多两行，完整说明留在 title 里（行高参差会让人找不到下一项）', () => {
    renderPicker([]);

    const desc = screen.getByText('查看任务列表与任务详情');
    expect(desc.className).toMatch(/line-clamp-2/);
    expect(desc.getAttribute('title')).toBe('查看任务列表与任务详情');
  });

  it('搜索框能按接口名或权限码过滤', async () => {
    const user = userEvent.setup();
    renderPicker([]);

    await user.type(screen.getByLabelText('搜索接口权限'), '导出');

    // 命中的还在
    expect(screen.getByRole('checkbox', { name: '查看导出 接口权限' })).toBeInTheDocument();
    // 不相关的被过滤掉
    expect(screen.queryByRole('checkbox', { name: '查看任务 接口权限' })).not.toBeInTheDocument();
  });

  it('搜不到时给出明确提示', async () => {
    const user = userEvent.setup();
    renderPicker([]);

    await user.type(screen.getByLabelText('搜索接口权限'), 'zzz不存在');

    expect(screen.getByText('没有匹配的接口权限')).toBeInTheDocument();
  });
});
