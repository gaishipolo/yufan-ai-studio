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
          {
            code: 'page:studio',
            name: '生成工作台',
            description: '关联接口：task:create',
            requires: ['task:create'],
            route: '/studio',
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

  /** 页面卡片的 DOM：从它的勾选框往上找到 <section>。 */
  const cardOf = (pageName: string) => {
    const box = screen.getByRole('checkbox', { name: `${pageName} 页面权限` });
    const card = box.closest('section');
    expect(card).not.toBeNull();
    return card as HTMLElement;
  };

  it('一个页面一张卡片，它管的接口就在卡片里', () => {
    renderPicker([]);

    const card = cardOf('任务中心');
    // 归属由**容器**表达：接口在谁的卡片里就归谁，不需要再写「归 X 管」
    expect(within(card).getByRole('checkbox', { name: '查看任务 接口权限' })).toBeInTheDocument();
    expect(within(card).getByRole('checkbox', { name: '创建任务 接口权限' })).toBeInTheDocument();
    // 别的页的接口不会跑到这一卡里
    expect(within(card).queryByRole('checkbox', { name: '查看资产库 接口权限' })).toBeNull();
  });

  it('共享接口在两张卡片里都出现（状态只有一份）', () => {
    renderPicker(['task:create']);

    const inTask = within(cardOf('任务中心')).getByRole('checkbox', { name: '创建任务 接口权限' });
    const inStudio = within(cardOf('生成工作台')).getByRole('checkbox', { name: '创建任务 接口权限' });
    // 两处都是勾着的：它们读的是同一份 selected
    expect(inTask).toBeChecked();
    expect(inStudio).toBeChecked();
  });

  it('页面没勾时，那一卡的正文变淡 —— 「勾了也不生效」由容器表达', () => {
    renderPicker(['task:read']);
    expect(cardOf('任务中心').querySelector('ul')?.className).toMatch(/opacity-55/);
  });

  it('开了的页面不淡（淡的是「这一页没开」，不是全局样式）', () => {
    renderPicker(['page:library', 'task:read']);
    expect(cardOf('资产库').querySelector('ul')?.className).not.toMatch(/opacity-55/);
  });

  it('页面勾上之后，同一张卡不再变淡（勾是唯一开关）', () => {
    renderPicker(['page:task-center', 'task:read']);
    expect(cardOf('任务中心').querySelector('ul')?.className).not.toMatch(/opacity-55/);
  });

  it('没有任何页面声明的接口归到「后台能力」卡（它不受页面开关影响）', () => {
    renderPicker([]);

    const box = screen.getByRole('checkbox', { name: '查看导出 接口权限' });
    const card = box.closest('section');
    expect(within(card as HTMLElement).getByText('后台能力')).toBeInTheDocument();
    // 没有页面管它 → 不会因为任何页面没开而变淡
    expect(card?.querySelector('ul')?.className).not.toMatch(/opacity-55/);
  });

  it('摘要条分开报页面与接口的个数', () => {
    renderPicker(['page:task-center', 'task:read']);

    // 「页面 1」「接口 1」各自是一个独立计数，合成一个总数就看不出层级
    const page = screen.getByText('页面').parentElement;
    const api = screen.getByText('接口').parentElement;
    expect(page?.textContent).toContain('1');
    expect(api?.textContent).toContain('1');
  });

  it('别的层已授予的权限：勾上但灰色不可改', () => {
    render(
      <PermissionPicker
        selected={[]}
        onChange={vi.fn()}
        inherited={{ 'task:read': ['角色'], 'page:task-center': ['角色'] }}
      />,
    );

    // 页面：勾上、禁用，并标明来源（不然看不出这个勾不是本层给的）
    const pageCheckbox = screen.getByRole('checkbox', { name: '任务中心 页面权限' });
    expect(pageCheckbox).toBeChecked();
    expect(pageCheckbox).toBeDisabled();
    const header = cardOf('任务中心').querySelector('header') as HTMLElement;
    expect(within(header).getByText('来自 角色')).toBeInTheDocument();

    // 接口：同样勾上 + 禁用
    const itemCheckbox = screen.getByRole('checkbox', { name: '查看任务 接口权限' });
    expect(itemCheckbox).toBeChecked();
    expect(itemCheckbox).toBeDisabled();
  });

  it('锁定的码点不动（点它不产生任何变更）', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <PermissionPicker
        selected={[]}
        onChange={onChange}
        inherited={{ 'task:read': ['角色'] }}
      />,
    );

    await user.click(screen.getByRole('checkbox', { name: '查看任务 接口权限' }));

    expect(onChange).not.toHaveBeenCalled();
  });

  it('摘要条把「其他层已授」单列，不混进本层的计数', () => {
    render(
      <PermissionPicker
        selected={['task:create']}
        onChange={vi.fn()}
        inherited={{ 'task:read': ['角色'] }}
      />,
    );

    // 本层只授了 1 个接口
    const api = screen.getByText('接口').parentElement;
    expect(api?.textContent).toContain('1');

    // 锁定的那个单列，不计入本层
    const locked = screen.getByText('其他层已授').parentElement;
    expect(locked?.textContent).toContain('1');
  });

  it('勾选页面不会连带勾上接口（页面是可见性，接口是能力）', async () => {
    const user = userEvent.setup();
    const { onChange } = renderPicker([]);

    await user.click(screen.getByRole('checkbox', { name: '任务中心 页面权限' }));

    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange.mock.calls[0][0]).toEqual(['page:task-center']);
  });

  it('卡片上的「全选」把这页的接口一次给全（仍然不含页面码）', async () => {
    const user = userEvent.setup();
    const { onChange } = renderPicker(['page:task-center']);

    const card = cardOf('任务中心');
    await user.click(within(card).getByRole('button', { name: '全选' }));

    const sent = onChange.mock.calls[0][0] as string[];
    // 这一页的接口全给了；页面码本来就在选中集合里，不该被连带删掉
    expect(sent).toContain('task:read');
    expect(sent).toContain('task:create');
    expect(sent).toContain('page:task-center');
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

    // 页面禁授时不摆勾选框：这一层根本不决定页面可见性，
    // 摆一个灰掉的框会让人以为「差点就能点」
    expect(screen.queryByRole('checkbox', { name: '资产库 页面权限' })).toBeNull();
    // 但它的接口仍然可以单独授（卡片还在，只是页头不可勾）
    await user.click(screen.getByRole('checkbox', { name: '查看资产库 接口权限' }));
    expect(onChange).toHaveBeenCalledWith(['library:read']);
  });

  it('页面全部禁授时（团队层），卡片照旧在但它不淡 —— 这一层不决定页面开闭', () => {
    render(
      <PermissionPicker
        selected={[]}
        onChange={vi.fn()}
        forbidden={(code) => code.startsWith('page:')}
        forbiddenHint="团队层不能授予该权限"
      />,
    );

    expect(screen.queryByRole('checkbox', { name: '任务中心 页面权限' })).toBeNull();
    const card = screen
      .getByRole('checkbox', { name: '查看任务 接口权限' })
      .closest('section') as HTMLElement;
    expect(card).not.toBeNull();
    expect(within(card).getByText('任务中心')).toBeInTheDocument();
    expect(card.querySelector('ul')?.className).not.toMatch(/opacity-55/);
  });

  it('页面名完整显示，不被截断（曾经「模板封装」被截成「模…」）', () => {
    renderPicker([]);

    const name = within(cardOf('任务中心')).getByText('任务中心');
    // 截断是 CSS 层面的（truncate → text-overflow + ellipsis），
    // 断言类名比断言像素更可靠：像素要布局引擎，类名是意图。
    expect(name.className).not.toMatch(/truncate/);
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

    await user.type(screen.getByLabelText('搜索权限'), '导出');

    // 命中的还在
    expect(screen.getByRole('checkbox', { name: '查看导出 接口权限' })).toBeInTheDocument();
    // 不相关的被过滤掉
    expect(screen.queryByRole('checkbox', { name: '查看任务 接口权限' })).not.toBeInTheDocument();
  });

  it('搜不到时给出明确提示', async () => {
    const user = userEvent.setup();
    renderPicker([]);

    await user.type(screen.getByLabelText('搜索权限'), 'zzz不存在');

    expect(screen.getByText('没有匹配的权限点')).toBeInTheDocument();
  });
});

/* 回归：`task:*` 这类通配不在权限目录里（目录只有具体码），于是它原来
   一行都渲染不出来 —— 而汇总条把它算进「接口 N」，数字对不上；更要紧的是
   它能一次性覆盖十几个接口，撤的时候却找不到它在哪。 */
describe('目录外的已选码', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('通配会单独列出来，并说清它是什么', () => {
    renderPicker(['page:task-center', 'task:*']);

    expect(screen.getByText('目录外')).toBeInTheDocument();
    const row = screen.getByRole('checkbox', { name: 'task:* 接口权限' }).closest('li');
    expect(row).not.toBeNull();
    expect(within(row as HTMLElement).getByText('task:*')).toBeInTheDocument();
    expect(
      within(row as HTMLElement).getByText('整类授权：覆盖该资源下现有的全部接口'),
    ).toBeInTheDocument();
  });

  it('计数与可见行一致：不会出现「接口 1」却一行都看不到', () => {
    renderPicker(['task:*']);

    expect(screen.getByRole('checkbox', { name: 'task:* 接口权限' })).toBeInTheDocument();
    // 汇总条仍然把它算作 1 个接口权限 —— 现在它同时有一行可点了
    expect(screen.getByText('接口')).toBeInTheDocument();
  });

  it('取消勾选能把通配真的撤掉', async () => {
    const user = userEvent.setup();
    const { onChange } = renderPicker(['task:*']);

    await user.click(screen.getByRole('checkbox', { name: 'task:* 接口权限' }));
    expect(onChange).toHaveBeenCalledWith([]);
  });

  it('目录里的码不会跑进「目录外」', () => {
    renderPicker(['task:read']);
    expect(screen.queryByText('目录外')).not.toBeInTheDocument();
  });
});
