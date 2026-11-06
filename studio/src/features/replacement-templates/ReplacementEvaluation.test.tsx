import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ReplacementRun } from '@/entities/template/model/replacement.types';
import { AccessProvider } from '@/shared/access/AccessProvider';
import { ReplacementEvaluation } from './ReplacementEvaluation';

const mutation = vi.hoisted(() => ({mutateAsync: vi.fn(), isPending: false}));
vi.mock('@/entities/template/api/replacement.queries', () => ({useReviewReplacement: () => mutation}));

function result(version = 0): ReplacementRun {
  return {id: 'run1', kind: 'character', target: '人物', status: 'success', archivePath: 'archive',
    createdAt: '2026-11-06T00:00:00Z', prompt: '', reviewStatus: version ? 'accepted' : 'pending',
    outputUrl: '/output', review: {status: version ? 'accepted' : 'pending', version}};
}
interface AppProps { run?: ReplacementRun; permissions?: string[] }
function App({run = result(), permissions = ['template:review']}: AppProps) {
  return <AccessProvider subject={{id: 'owner', name: '用户', roles: [], permissions}}><ReplacementEvaluation run={run} /></AccessProvider>;
}

beforeEach(() => {mutation.mutateAsync.mockReset(); mutation.isPending = false;});

describe('成片人工验收', () => {
  it('只有成功且有结果、并有评价权限时显示操作', () => {
    const view = render(<App permissions={[]} />);
    expect(screen.getByRole('heading')).toHaveTextContent('待验收');
    expect(screen.queryByRole('button', {name: '评价成片'})).not.toBeInTheDocument();
    view.rerender(<App run={{...result(), status: 'running'}} />);
    expect(screen.queryByRole('button', {name: '评价成片'})).not.toBeInTheDocument();
    view.rerender(<App run={{...result(), outputUrl: null}} />);
    expect(screen.queryByRole('button', {name: '评价成片'})).not.toBeInTheDocument();
  });

  it('不可用必须选择原因，保存失败保留草稿且不宣称成功', async () => {
    mutation.mutateAsync.mockRejectedValue(new Error('连接中断'));
    render(<App />);
    fireEvent.click(screen.getByRole('button', {name: '评价成片'}));
    fireEvent.change(screen.getByRole('combobox', {name: '可用性结论'}), {target: {value: 'rejected'}});
    expect(screen.getByRole('button', {name: '保存评价'})).toBeDisabled();
    fireEvent.click(screen.getByRole('checkbox', {name: '画面异常'}));
    fireEvent.change(screen.getByRole('textbox', {name: '评价说明（选填）'}), {target: {value: '手部变形'}});
    fireEvent.click(screen.getByRole('button', {name: '保存评价'}));
    expect(await screen.findByRole('alert')).toHaveTextContent('连接中断');
    expect(screen.getByRole('textbox')).toHaveValue('手部变形');
    expect(screen.queryByText('评价已保存。')).not.toBeInTheDocument();
    expect(screen.getByRole('heading')).toHaveTextContent('待验收');
    expect(mutation.mutateAsync).toHaveBeenCalledWith({id: 'run1', payload: {status: 'rejected', expectedVersion: 0, reasonCodes: ['visual'], comment: '手部变形'}});
  });

  it('版本变化要求重新核对，保留草稿并用新版本保存', async () => {
    mutation.mutateAsync.mockResolvedValue(result(2));
    const view = render(<App />);
    fireEvent.click(screen.getByRole('button', {name: '评价成片'}));
    fireEvent.change(screen.getByRole('textbox'), {target: {value: '复核意见'}});
    view.rerender(<App run={result(1)} />);
    expect(screen.getByRole('button', {name: '保存评价'})).toBeDisabled();
    expect(screen.getByRole('textbox')).toHaveValue('复核意见');
    fireEvent.click(screen.getByRole('button', {name: '已核对最新结论，保留草稿'}));
    fireEvent.click(screen.getByRole('button', {name: '保存评价'}));
    expect(await screen.findByText('评价已保存。')).toBeVisible();
    expect(mutation.mutateAsync.mock.calls[0][0].payload.expectedVersion).toBe(1);
  });

  it('等待服务端响应期间不显示已保存，禁用提交及取消', async () => {
    let resolve: ((value: ReplacementRun) => void) | undefined;
    mutation.mutateAsync.mockImplementation(() => new Promise<ReplacementRun>(done => {resolve = done;}));
    const view = render(<App />);
    fireEvent.click(screen.getByRole('button', {name: '评价成片'}));
    fireEvent.click(screen.getByRole('button', {name: '保存评价'}));
    mutation.isPending = true;
    view.rerender(<App />);
    expect(screen.getByRole('button', {name: '保存评价'})).toBeDisabled();
    expect(screen.getByRole('button', {name: '取消'})).toBeDisabled();
    expect(screen.queryByText('评价已保存。')).not.toBeInTheDocument();
    await act(async () => {resolve?.(result(1));});
    expect(screen.getByText('评价已保存。')).toBeVisible();
  });
});
