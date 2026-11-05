import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import type { ReplacementRun } from '@/entities/template/model/replacement.types';
import { ReplacementTemplates } from './ReplacementTemplates';

const mock = vi.hoisted(() => ({
  prepare: vi.fn(), execute: vi.fn(), mediaUrls: [] as string[],
  catalog: { data: { root: 'Z:/yufan_ai_studio', packs: [], runs: [] as ReplacementRun[] }, refetch: vi.fn() },
}));
vi.mock('@/entities/asset/api/archive.queries', () => ({
  useArchiveAssets: () => ({ data: { items: [
    { id: 'source', name: '测试原片', category: 'source', type: 'video', duration: 2, hasAudio: true,
      url: '/api/v1/assets/archive/source/file' },
    { id: 'source-15s', name: '十五秒原片', category: 'source', type: 'video', duration: 15, hasAudio: true,
      url: '/api/v1/assets/archive/source-15s/file' },
    { id: 'image', name: '人物参考', category: 'character', type: 'image' },
  ] } }),
  useArchiveUpload: () => ({ isPending: false }),
  useArchivedMedia: (url?: string) => {if (url) mock.mediaUrls.push(url); return {data: undefined};},
}));
vi.mock('@/entities/template/api/replacement.queries', () => ({
  useReplacementCatalog: () => mock.catalog,
  usePrepareReplacement: () => ({ mutateAsync: mock.prepare }),
  useExecuteReplacement: () => ({ mutateAsync: mock.execute, isPending: false }),
}));
vi.mock('@/shared/access/Can', () => ({ Can: ({ children }: { children: ReactNode }) => children }));
vi.mock('@/shared/lib/toast', () => ({ toast: vi.fn() }));

function Location() { return <output aria-label="地址">{useLocation().search}</output>; }
function App({ url = '/templates' }: { url?: string }) {
  return <MemoryRouter initialEntries={[url]}><ReplacementTemplates /><Location /></MemoryRouter>;
}
function run(status: string): ReplacementRun {
  return { id: 'run1', kind: 'character', target: '测试人物', status, archivePath: 'Z:/runs/run1',
    createdAt: '2026-11-05T00:00:00Z', prompt: '最终提示词', taskId: 'existing', progress: 50,
    reviewStatus: 'pending' };
}

beforeEach(() => {
  mock.prepare.mockReset();
  mock.execute.mockReset();
  mock.mediaUrls.length = 0;
  mock.catalog.data.runs = [];
});

describe('替换模板工作台', () => {
  it('从 URL 恢复类型与搜索，清空搜索同步地址', () => {
    render(<App url="/templates?kind=product&q=原片" />);
    expect(screen.getByRole('button', { name: '产品替换' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('searchbox')).toHaveValue('原片');
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: '' } });
    expect(screen.getByLabelText('地址')).toHaveTextContent('?kind=product');
  });

  it('15 秒原片可选，悬停预览走浏览器兼容流而不是原始归档文件', () => {
    render(<App />);
    const card = screen.getByRole('button', { name: '选择模板：十五秒原片' });
    expect(card).toBeEnabled();
    fireEvent.mouseEnter(card.closest('article')!);
    expect(mock.mediaUrls).toContain('/api/v1/assets/archive/source-15s/stream');
    expect(mock.mediaUrls).not.toContain('/api/v1/assets/archive/source-15s/file');
  });

  it('准备失败后复用请求 ID，避免在新目录重复归档', async () => {
    mock.prepare.mockRejectedValue(new Error('模拟断连'));
    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: '选择模板：测试原片' }));
    fireEvent.change(screen.getByRole('textbox', { name: '目标名称' }), { target: { value: '测试人物' } });
    fireEvent.click(screen.getByRole('checkbox', { name: '人物参考' }));
    const prepare = screen.getByRole('button', { name: '预览提示词并归档输入' });
    fireEvent.click(prepare);
    await screen.findByRole('button', { name: '预览提示词并归档输入' });
    expect(mock.prepare).toHaveBeenCalledTimes(1);
    fireEvent.click(prepare);
    await vi.waitFor(() => expect(mock.prepare).toHaveBeenCalledTimes(2));
    expect(mock.prepare.mock.calls[1][0].requestId).toBe(mock.prepare.mock.calls[0][0].requestId);
    expect(mock.execute).not.toHaveBeenCalled();
  });

  it('打开的档案跟随查询更新，已有任务只提供继续查询入口', () => {
    mock.catalog.data.runs = [run('running')];
    const view = render(<App />);
    fireEvent.click(screen.getByRole('button', { name: /测试人物.*生成中/ }));
    expect(within(screen.getByRole('dialog')).getByRole('button', { name: '继续查询已有任务' })).toBeVisible();
    mock.catalog.data.runs = [run('success')];
    view.rerender(<App />);
    expect(within(screen.getByRole('dialog')).getByText('测试人物 · 已完成 · 待验收')).toBeVisible();
    expect(screen.queryByRole('button', { name: '继续查询已有任务' })).not.toBeInTheDocument();
  });
});
