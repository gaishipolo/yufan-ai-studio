import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { PackManaged } from '@/shared/api/schema';
import { PackEditor } from './PackEditor';

const mock = vi.hoisted(() => ({
  save: vi.fn(), action: vi.fn(),
  items: [
    {id: 'p1', name: '产品正面', previewUrl: '/thumb/p1'},
    {id: 'p2', name: '产品三视图', previewUrl: '/thumb/p2'},
    {id: 'p3', name: '产品细节', previewUrl: '/thumb/p3'},
  ],
}));

vi.mock('@/entities/asset/api/archive.queries', () => ({
  useArchiveAssets: () => ({data: {items: mock.items, total: 3}, isPending: false, error: null, refetch: vi.fn()}),
  useArchivedMedia: () => ({data: undefined, error: null}),
}));
vi.mock('@/entities/template/api/pack.queries', () => ({
  usePackMutations: () => ({
    save: {mutateAsync: mock.save, isPending: false, error: null},
    action: {mutateAsync: mock.action, isPending: false, error: null},
  }),
}));

const pack: PackManaged = {
  id: 'a'.repeat(32), kind: 'product', name: '3030', referenceIds: ['p1', 'p3'],
  coverReferenceId: 'p1', version: 1, revision: 3, status: 'published', gaps: [],
};

beforeEach(() => {
  mock.save.mockReset().mockResolvedValue({id: 'a'.repeat(32), revision: 4});
  mock.action.mockReset().mockResolvedValue({});
});

describe('封装编辑器', () => {
  it('旧包没有用途时按基准用途展示，不显示为未设置', () => {
    render(<PackEditor pack={{...pack, bindings: undefined}} onClose={vi.fn()} />);
    expect(screen.getByLabelText('第 1 张的用途')).toHaveValue('front');
    expect(screen.getByLabelText('第 2 张的用途')).toHaveValue('front');
  });

  it('草稿不完整时不能直接发布，并列出缺什么', () => {
    render(<PackEditor pack={null} onClose={vi.fn()} />);
    expect(screen.getByRole('button', {name: '保存并发布'})).toBeDisabled();
    const check = screen.getByLabelText('发布前检查');
    expect(within(check).getByText('对象名称')).toBeVisible();
    expect(within(check).getByText('1–10 张参考图')).toBeVisible();
  });

  it('补齐名称、用途和范围后按顺序保存并发布，用途随参考图一起提交', async () => {
    const onClose = vi.fn();
    render(<PackEditor pack={null} onClose={onClose} />);
    fireEvent.change(screen.getByLabelText('对象名称'), {target: {value: '3030 粉色'}});
    fireEvent.click(screen.getByRole('button', {name: /产品正面/}));
    fireEvent.click(screen.getByRole('button', {name: /产品细节/}));
    fireEvent.change(screen.getByLabelText('第 2 张的用途'), {target: {value: 'detail'}});
    fireEvent.click(screen.getByLabelText('产品细节图'));
    fireEvent.click(screen.getByLabelText('人物上身穿着'));
    fireEvent.click(screen.getByLabelText('手持展示产品'));

    const publish = screen.getByRole('button', {name: '保存并发布'});
    expect(publish).toBeEnabled();
    fireEvent.click(publish);

    await vi.waitFor(() => expect(mock.action).toHaveBeenCalledOnce());
    const payload = mock.save.mock.calls[0][0];
    expect(payload.name).toBe('3030 粉色');
    expect(payload.referenceIds).toEqual(['p1', 'p3']);
    expect(payload.bindings).toEqual([
      {role: 'front', description: ''},
      {role: 'detail', description: ''},
    ]);
    expect(payload.supportedScopes).toEqual(['worn', 'held']);
    expect(mock.action.mock.calls[0][0]).toMatchObject({id: 'a'.repeat(32), action: 'publish', expectedRevision: 4});
    await vi.waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('只保存草稿时不发布', async () => {
    render(<PackEditor pack={pack} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', {name: '保存草稿'}));
    await vi.waitFor(() => expect(mock.save).toHaveBeenCalledOnce());
    expect(mock.action).not.toHaveBeenCalled();
  });
});
