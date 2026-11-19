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
          { code: 'task:read', name: '查看任务', description: '' },
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

    // 页面卡片上直接写着关联接口，不用去别处找
    const card = screen.getByRole('checkbox', { name: '任务中心 页面权限' }).closest('li');
    expect(card).not.toBeNull();
    expect(within(card as HTMLElement).getByTitle('查看任务')).toBeInTheDocument();
    expect(within(card as HTMLElement).getByTitle('创建任务')).toBeInTheDocument();
  });

  it('接口标出它被哪些页面管着', () => {
    renderPicker(['page:task-center']);

    const item = screen.getByRole('checkbox', { name: '查看任务 接口权限' }).closest('li');
    expect(item).not.toBeNull();
    expect(within(item as HTMLElement).getByText(/随 任务中心 生效/)).toBeInTheDocument();
  });

  it('页面没开的时候，接口标成「不生效」——勾之前就能看见', () => {
    renderPicker(['task:read']);

    const item = screen.getByRole('checkbox', { name: '查看任务 接口权限' }).closest('li');
    expect(item).not.toBeNull();
    expect(
      within(item as HTMLElement).getByText(/页面未开启时不生效（任务中心）/),
    ).toBeInTheDocument();
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

  it('勾选页面不会连带勾上接口（页面是可见性，接口是能力）', async () => {
    const user = userEvent.setup();
    const { onChange } = renderPicker([]);

    await user.click(screen.getByRole('checkbox', { name: '任务中心 页面权限' }));

    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange.mock.calls[0][0]).toEqual(['page:task-center']);
  });

  it('禁用的码点不动（团队层禁页面码）', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <PermissionPicker
        selected={[]}
        onChange={onChange}
        forbidden={(code) => code.startsWith('page:')}
        forbiddenHint="团队层不能授予该权限"
      />,
    );

    const checkbox = screen.getByRole('checkbox', { name: '任务中心 页面权限' });
    expect(checkbox).toBeDisabled();
    await user.click(checkbox);
    expect(onChange).not.toHaveBeenCalled();
  });
});
