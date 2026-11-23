import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { PermissionOverview } from './PermissionOverview';
import { buildOverview } from './use-overview';

/* 目录里两页 + 一个共享接口 + 一个后台能力：
   - `task:read` 只归「任务中心」，`task:create` 同时归两页（共享）
   - `export:read` 没有页面管（后台能力，不受页面开关影响） */
const CATALOG = {
  groups: [
    {
      group: '页面',
      items: [
        {
          code: 'page:task-center',
          name: '任务中心',
          description: '',
          requires: ['task:read', 'task:create'],
          route: '/tasks',
        },
        {
          code: 'page:library',
          name: '资产库',
          description: '',
          requires: ['task:create', 'library:create'],
          route: '/library',
        },
      ],
    },
    {
      group: '内容生产',
      items: [
        { code: 'task:read', name: '查看任务', description: '' },
        { code: 'task:create', name: '创建任务', description: '' },
        { code: 'library:create', name: '上传素材', description: '' },
        { code: 'export:read', name: '查看导出', description: '' },
      ],
    },
  ],
};

function data(overrides: Record<string, unknown> = {}) {
  return {
    team: [],
    groups: [],
    personal: [],
    roles: [],
    pages: ['page:task-center', 'page:library'],
    gatedOff: [],
    deniedOff: [],
    denied: [],
    effective: ['task:read', 'task:create', 'library:create', 'export:read'],
    sources: [],
    ...overrides,
  };
}

describe('buildOverview：把出参摊成行', () => {
  it('共享接口只出现一行（否则计数会翻倍，也会让人以为有两条权限）', () => {
    const view = buildOverview(data(), CATALOG);

    const rows = view.groups.flatMap((group) => group.rows.map((row) => row.code));
    expect(rows.filter((code) => code === 'task:create')).toHaveLength(1);
  });

  it('挂到第一个声明它的页面下，并标出「多页共用」', () => {
    const view = buildOverview(data(), CATALOG);
    const taskCenter = view.groups.find((group) => group.code === 'page:task-center');

    const shared = taskCenter?.rows.find((row) => row.code === 'task:create');
    expect(shared?.shared).toBe(true);
    // 只归一个页面的不算共享
    expect(taskCenter?.rows.find((row) => row.code === 'task:read')?.shared).toBe(false);
  });

  it('状态优先级：被本人关掉 > 页面没开 > 生效', () => {
    const view = buildOverview(
      data({
        effective: ['task:create', 'library:create'],
        denied: ['task:read'],
        deniedOff: ['task:read'],
        gatedOff: ['library:create'],
        pages: ['page:task-center'],
      }),
      CATALOG,
    );

    const rows = new Map(
      view.groups.flatMap((group) => group.rows.map((row) => [row.code, row.status])),
    );
    expect(rows.get('task:read')).toBe('denied');
    expect(rows.get('library:create')).toBe('gated');
    expect(rows.get('task:create')).toBe('effective');
  });

  it('页面没开时整组标记关闭（组内行由容器表达「勾了也不生效」）', () => {
    const view = buildOverview(
      data({
        pages: ['page:task-center'],
        effective: ['task:read', 'library:create'],
        gatedOff: ['library:create'],
      }),
      CATALOG,
    );

    expect(view.groups.find((group) => group.code === 'page:task-center')?.open).toBe(true);
    expect(view.groups.find((group) => group.code === 'page:library')?.open).toBe(false);
  });

  it('例外段含「当前无人授予」的码 —— 否则那条例外永远看不见、也撤销不了', () => {
    const view = buildOverview(
      data({ effective: [], denied: ['export:read'] }),
      CATALOG,
    );

    expect(view.exceptions.map((row) => row.code)).toEqual(['export:read']);
    expect(view.exceptions[0].status).toBe('denied');
  });

  it('计数不含页面码（页面不是「一项权限」）', () => {
    const view = buildOverview(
      data({ effective: ['page:task-center', 'task:read'], pages: ['page:task-center'] }),
      CATALOG,
    );

    expect(view.counts.effective).toBe(1);
  });

  it('没有页面管的码归到后台能力', () => {
    const view = buildOverview(data(), CATALOG);
    expect(view.background.map((row) => row.code)).toEqual(['export:read']);
  });
});

describe('PermissionOverview：一行一个动作，由来源决定', () => {
  const renderOverview = (overrides: Record<string, unknown> = {}) => {
    const view = buildOverview(
      data({
        sources: [
          { code: 'task:read', layers: [{ kind: 'role', id: 'r1', name: '内容编辑' }] },
          { code: 'task:create', layers: [{ kind: 'role', id: 'r1', name: '内容编辑' }] },
          { code: 'library:create', layers: [{ kind: 'user', name: '个人' }] },
        ],
        ...overrides,
      }),
      CATALOG,
    );
    const onDeny = vi.fn();
    const onAllow = vi.fn();
    const onRestore = vi.fn();
    render(
      <PermissionOverview
        overview={view}
        onDeny={onDeny}
        onAllow={onAllow}
        onRestore={onRestore}
      />,
    );
    return { onDeny, onAllow, onRestore };
  };

  const row = (code: string) => {
    const node = document.querySelector(`[data-permission-row="${code}"]`);
    if (!node) throw new Error(`找不到 ${code} 这一行`);
    return node as HTMLElement;
  };

  it('来源含共享单元 → 仅对此人关闭（改角色会连累别人）', async () => {
    const user = userEvent.setup();
    const { onDeny } = renderOverview();

    await user.click(within(row('task:read')).getByRole('button', { name: '仅对此人关闭' }));
    expect(onDeny).toHaveBeenCalledWith(expect.objectContaining({ code: 'task:read' }));
  });

  it('来源只有个人 → 移除（不需要留一条例外）', async () => {
    const user = userEvent.setup();
    const { onAllow, onDeny } = renderOverview();

    await user.click(within(row('library:create')).getByRole('button', { name: '移除' }));
    expect(onAllow).toHaveBeenCalledWith(expect.objectContaining({ code: 'library:create' }));
    expect(onDeny).not.toHaveBeenCalled();
  });

  it('已关闭的行仍在列表里，动作是「恢复」', async () => {
    const user = userEvent.setup();
    const { onRestore } = renderOverview({
      denied: ['task:read'],
      deniedOff: ['task:read'],
    });

    // 不消失是关键：消失了用户会以为「删了」，而它其实是可恢复的
    expect(within(row('task:read')).getByText('已对他关闭')).toBeInTheDocument();
    await user.click(within(row('task:read')).getByRole('button', { name: '恢复' }));
    expect(onRestore).toHaveBeenCalledWith(expect.objectContaining({ code: 'task:read' }));
  });

  it('页面没开时组内不给动作（先开页面，点这里没用）', () => {
    renderOverview({ pages: [], effective: [], gatedOff: ['task:read', 'task:create'] });

    const group = document.querySelector('[data-page-group="page:task-center"]') as HTMLElement;
    expect(within(group).getByText('页面没开')).toBeInTheDocument();
    expect(within(group).queryByRole('button', { name: '仅对此人关闭' })).toBeNull();
  });

  it('搜索命中时保留父级分组（否则不知道命中的是哪一页的）', async () => {
    const user = userEvent.setup();
    renderOverview();

    await user.type(screen.getByLabelText('搜索权限'), '上传');

    expect(document.querySelector('[data-page-group="page:library"]')).not.toBeNull();
    expect(document.querySelector('[data-page-group="page:task-center"]')).toBeNull();
  });

  it('筛选「仅例外」只留被关掉的行', async () => {
    const user = userEvent.setup();
    renderOverview({ denied: ['task:read'], deniedOff: ['task:read'] });

    // 筛选是分段控件（role=radio），不是按钮组
    await user.click(screen.getByRole('radio', { name: '仅例外' }));

    expect(document.querySelector('[data-permission-row="task:read"]')).not.toBeNull();
    expect(document.querySelector('[data-permission-row="task:create"]')).toBeNull();
  });
});
