import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { TagOut } from '@/shared/api/schema';
import { TemplateTagManager } from './TemplateTagManager';

/**
 * 模板词表管理：写操作的对外契约。
 *
 * 守卫点：只能挂在一级维度下；有下级的维度不能移动；删除必须两段确认。
 * 这三条任何一条退化，都会让「改名顺手改了树」「删维度丢了一串取值」。
 */
const { calls } = vi.hoisted(() => ({
  calls: {
    create: [] as { name: string; parentId?: string | null }[],
    rename: [] as { id: string; name: string }[],
    move: [] as { id: string; parentId: string | null }[],
    remove: [] as string[],
  },
}));

vi.mock('@/entities/template-spec/api/spec.queries', () => ({
  useTemplateTagMutations: () => ({
    create: {
      mutateAsync: (payload: { name: string; parentId?: string | null }) => {
        calls.create.push(payload);
        return Promise.resolve({ id: 'new', ...payload });
      },
      isPending: false,
    },
    rename: {
      mutateAsync: ({ id, payload }: { id: string; payload: { name: string } }) => {
        calls.rename.push({ id, name: payload.name });
        return Promise.resolve({ id });
      },
      isPending: false,
    },
    move: {
      mutateAsync: ({ id, payload }: { id: string; payload: { parentId: string | null } }) => {
        calls.move.push({ id, parentId: payload.parentId });
        return Promise.resolve({ id });
      },
      isPending: false,
    },
    remove: {
      mutateAsync: (id: string) => {
        calls.remove.push(id);
        return Promise.resolve(null);
      },
      isPending: false,
    },
  }),
}));

/** 两级词表：产品（维度）下挂 6036 / T35；风格下挂写实。 */
const TAGS: TagOut[] = [
  { id: 't1', name: '产品', depth: 1, specCount: 4 },
  { id: 't11', name: '6036', parentId: 't1', parentName: '产品', depth: 2, specCount: 2 },
  { id: 't12', name: 'T35', parentId: 't1', parentName: '产品', depth: 2, specCount: 2 },
  { id: 't2', name: '风格', depth: 1, specCount: 3 },
  { id: 't21', name: '写实', parentId: 't2', parentName: '风格', depth: 2, specCount: 3 },
];

beforeEach(() => {
  calls.create.length = 0;
  calls.rename.length = 0;
  calls.move.length = 0;
  calls.remove.length = 0;
});

function renderManager(tags: TagOut[] = TAGS) {
  render(<TemplateTagManager tags={tags} onClose={vi.fn()} />);
}

describe('TemplateTagManager · 模板词表结构', () => {
  it('能新建一级维度，也能挂到二级取值', async () => {
    const user = userEvent.setup();
    renderManager();

    await user.type(screen.getByRole('textbox', { name: '新标签名称' }), '场景{enter}');
    expect(calls.create).toEqual([{ name: '场景', parentId: undefined }]);

    await user.selectOptions(screen.getByRole('combobox', { name: '新标签所属上级' }), 't1');
    await user.type(screen.getByRole('textbox', { name: '新标签名称' }), 'T40{enter}');
    expect(calls.create.at(-1)).toEqual({ name: 'T40', parentId: 't1' });
  });

  it('上级下拉只有一级维度：把取值当上级会造出第三层', () => {
    renderManager();
    const select = screen.getByRole('combobox', { name: '新标签所属上级' });
    expect(select.querySelector('option[value="t11"]')).not.toBeInTheDocument();
    expect(select.querySelector('option[value="t1"]')).not.toBeNull();
  });

  it('改名单独走 rename mutation，不改层级', async () => {
    const user = userEvent.setup();
    renderManager();
    const input = screen.getByRole('textbox', { name: '6036 的名称' });
    await user.clear(input);
    await user.type(input, '6036 Pro');
    const row = input.closest('div.flex.flex-wrap') as HTMLElement;
    await user.click(within(row).getByRole('button', { name: '改名' }));

    expect(calls.rename).toEqual([{ id: 't11', name: '6036 Pro' }]);
  });

  it('有下级的维度禁移动；叶子取值可以移动到别的维度', async () => {
    const user = userEvent.setup();
    renderManager();

    const topSelect = screen.getByRole('combobox', { name: '产品 的层级' });
    expect(topSelect).toBeDisabled();

    const leafSelect = screen.getByRole('combobox', { name: '写实 的层级' });
    await user.selectOptions(leafSelect, 't1');
    expect(calls.move).toEqual([{ id: 't21', parentId: 't1' }]);
  });

  it('删除两段式：先点头像只进入确认，确认按钮才真的删', async () => {
    const user = userEvent.setup();
    renderManager();

    await user.click(screen.getByRole('button', { name: '删除标签 T35' }));
    expect(calls.remove).toEqual([]);
    expect(screen.getByText('删除后不可恢复')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '确认删除' }));
    expect(calls.remove).toEqual(['t12']);
  });

  it('删维度时把「下级会提升一级」的后果写在确认处', async () => {
    const user = userEvent.setup();
    renderManager();

    await user.click(screen.getByRole('button', { name: '删除标签 产品' }));
    expect(screen.getByText(/2 个下级会提升一级/)).toBeInTheDocument();
    expect(calls.remove).toEqual([]);
  });
});
