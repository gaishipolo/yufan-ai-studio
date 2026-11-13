import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { PackManaged } from '@/shared/api/schema';
import { PackEditor } from './PackEditor';

const mock = vi.hoisted(() => ({
  save: vi.fn(), action: vi.fn(), packDetail: vi.fn(),
  /* 旧素材库候选（编辑既有 v1 封装时才读） */
  items: [
    {id: 'p1', name: '产品正面', previewUrl: '/thumb/p1'},
    {id: 'p2', name: '产品三视图', previewUrl: '/thumb/p2'},
    {id: 'p3', name: '产品细节', previewUrl: '/thumb/p3'},
  ],
  /* 资产库 v2 候选（新建 / 编辑 v2 封装） */
  libraryItems: [
    {id: 'l1', name: '库-产品正面', previewUrl: '/api/v1/library/assets/l1/thumbnail'},
    {id: 'l2', name: '库-产品三视图', previewUrl: null},
    {id: 'l3', name: '库-产品细节', previewUrl: '/api/v1/library/assets/l3/thumbnail'},
  ],
  /* 资产库封装（「从封装导入」的候选） */
  packs: [{id: 'pack-1', name: '3030 三件套'}],
}));

vi.mock('@/entities/asset/api/archive.queries', () => ({
  useArchiveAssets: () => ({data: {items: mock.items, total: 3}, isPending: false, error: null, refetch: vi.fn()}),
  useArchivedMedia: () => ({data: undefined, error: null}),
}));
vi.mock('@/entities/library/api/library.queries', () => ({
  useLibrarySelection: (params: {kind?: string} = {}) => params.kind === 'pack'
    ? {data: {items: mock.packs, total: 1}, isPending: false, error: null, refetch: vi.fn()}
    : {data: {items: mock.libraryItems, total: 3}, isPending: false, error: null, refetch: vi.fn()},
}));
vi.mock('@/entities/library/api/library.api', () => ({
  libraryApi: {packDetail: mock.packDetail},
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
  referenceIdSpace: 'v1',
};

beforeEach(() => {
  mock.save.mockReset().mockResolvedValue({id: 'a'.repeat(32), revision: 4});
  mock.action.mockReset().mockResolvedValue({});
  mock.packDetail.mockReset();
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

  it('新建封装从资产库选图，按顺序保存并发布，字段用 referenceLibraryIds', async () => {
    const onClose = vi.fn();
    render(<PackEditor pack={null} onClose={onClose} />);
    fireEvent.change(screen.getByLabelText('对象名称'), {target: {value: '3030 粉色'}});
    fireEvent.click(screen.getByRole('button', {name: /库-产品正面/}));
    fireEvent.click(screen.getByRole('button', {name: /库-产品细节/}));
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
    /* 新建一律写 v2：`referenceIds` 与 `referenceLibraryIds` 是两套 id 空间，
       混着写会让同一批 hex 被另一侧解释成别人的素材。 */
    expect(payload.referenceLibraryIds).toEqual(['l1', 'l3']);
    expect(payload.coverLibraryId).toBe('l1');
    expect(payload.referenceIds).toBeUndefined();
    expect(payload.bindings).toEqual([
      {role: 'front', description: ''},
      {role: 'detail', description: ''},
    ]);
    expect(payload.supportedScopes).toEqual(['worn', 'held']);
    expect(mock.action.mock.calls[0][0]).toMatchObject({id: 'a'.repeat(32), action: 'publish', expectedRevision: 4});
    await vi.waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('编辑旧素材库的封装仍写 referenceIds，不会被重新解释成 v2', async () => {
    render(<PackEditor pack={pack} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', {name: '保存草稿'}));
    await vi.waitFor(() => expect(mock.save).toHaveBeenCalledOnce());
    const payload = mock.save.mock.calls[0][0];
    expect(payload.referenceIds).toEqual(['p1', 'p3']);
    expect(payload.coverReferenceId).toBe('p1');
    expect(payload.referenceLibraryIds).toBeUndefined();
    expect(mock.action).not.toHaveBeenCalled();
  });

  it('从资产库封装导入成员：用途照成员角色走，认不出的退回基准并提示', async () => {
    mock.packDetail.mockResolvedValue({
      packItems: [
        {assetId: 'l2', name: '库-三视图', kind: 'image', state: 'active', position: 1, role: 'multiview', description: '左视 45°'},
        {assetId: 'l3', name: '库-细节', kind: 'image', state: 'active', position: 2, role: '', description: ''},
      ],
    });
    render(<PackEditor pack={null} onClose={vi.fn()} />);
    fireEvent.change(screen.getByLabelText('从资产库封装导入'), {target: {value: 'pack-1'}});
    await vi.waitFor(() => expect(screen.getByLabelText('第 1 张的用途')).toHaveValue('multiview'));
    /* 第 2 张的 role 是空串：不按位置猜成「主图」，而是退回基准用途并让人确认 */
    expect(screen.getByLabelText('第 2 张的用途')).toHaveValue('front');
    expect(screen.getByText(/用途需要你确认/)).toBeVisible();
    expect(screen.getByLabelText('第 1 张的补充说明')).toHaveValue('左视 45°');
    expect(mock.packDetail).toHaveBeenCalledWith('pack-1');
  });

  it('编辑 v2 封装写 referenceLibraryIds，字段名跟着空间走', async () => {
    render(<PackEditor pack={{...pack, referenceIds: ['l1', 'l2'], coverReferenceId: 'l1', referenceIdSpace: 'v2'}} onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', {name: '保存草稿'}));
    await vi.waitFor(() => expect(mock.save).toHaveBeenCalledOnce());
    const payload = mock.save.mock.calls[0][0];
    expect(payload.referenceLibraryIds).toEqual(['l1', 'l2']);
    expect(payload.coverLibraryId).toBe('l1');
    expect(payload.referenceIds).toBeUndefined();
  });
});
