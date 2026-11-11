import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ArchivedAsset, ArchiveCategory } from '@/entities/asset/model/archive.types';
import type { VideoTemplateOut } from '@/shared/api/schema';
import { AccessProvider } from '@/shared/access/AccessProvider';
import type { ReplacementChannelOut, ReplacementPack } from '@/shared/api/schema';
import type { ReplacementRun } from '@/entities/template/model/replacement.types';
import { ReplacementTemplates } from './ReplacementTemplates';

interface UploadCallbacks {
  onSuccess: (asset: ArchivedAsset) => void;
  onError: (reason: Error) => void;
}

const mock = vi.hoisted(() => ({
  prepare: vi.fn(), execute: vi.fn(), review: vi.fn(), manage: vi.fn(), mediaUrls: [] as string[],
  videoTemplates: [] as VideoTemplateOut[],
  assetsLoading: false, assetsError: null as Error | null, assetsHidden: false, assetsEmpty: false, assetsRefetch: vi.fn(),
  catalogHidden: false,
  upload: vi.fn<(input: {file: File; category: ArchiveCategory}, callbacks: UploadCallbacks) => void>(),
  uploadPending: false,
  catalog: { data: { root: 'Z:/yufan_ai_studio', packs: [] as ReplacementPack[], runs: [] as ReplacementRun[],
    packScopes: {worn: '人物上身穿着', held: '手持展示产品', boxed: '盒内可见产品', screen: '手机详情页产品图'},
    channels: undefined as ReplacementChannelOut[] | undefined, packRoles: {}, baseRoles: {} }, refetch: vi.fn(), isLoading: false, isFetching: false, error: null as Error | null },
}));
vi.mock('@/entities/asset/api/archive.queries', () => ({
  useArchiveSelection: () => ({data: undefined, error: null}),
  useArchiveAssets: () => ({ isLoading: mock.assetsLoading, error: mock.assetsError, refetch: mock.assetsRefetch, data: mock.assetsHidden ? undefined : { items: mock.assetsEmpty ? [] : [
    { id: 'source', name: '测试原片', category: 'source', type: 'video', duration: 2, hasAudio: true,
      url: '/api/v1/assets/source/file' },
    { id: 'source-15s', name: '十五秒原片', category: 'source', type: 'video', duration: 15, hasAudio: true,
      url: '/api/v1/assets/source-15s/file' },
    { id: 'image', name: '人物参考', category: 'character', type: 'image', url: '/image', previewUrl: '/thumbnail/image' },
    { id: 'product-front', name: '产品正面', category: 'product', type: 'image', url: '/product-front', previewUrl: '/thumbnail/product-front' },
    { id: 'product-back', name: '产品背面', category: 'product', type: 'image', url: '/product-back', previewUrl: '/thumbnail/product-back' },
    { id: 'product-detail', name: '产品细节', category: 'product', type: 'image', url: '/product-detail', previewUrl: '/thumbnail/product-detail' },
    { id: 'source-third', name: '第三原片', category: 'source', type: 'video', duration: 3, hasAudio: true, url: '/api/v1/assets/source-third/file' },
    { id: 'source-template', name: '我穿的是米兰之夜模板测试.mp4', category: 'source', type: 'video', duration: 5.31, hasAudio: true, url: '/api/v1/assets/source-template/file' },
  ] } }),
  useArchiveUpload: () => ({ isPending: mock.uploadPending, mutate: mock.upload }),
  useArchiveDownload: () => ({mutate: vi.fn(), isPending: false, error: null}),
  useArchivedMedia: (url?: string) => {if (url) mock.mediaUrls.push(url); return {data: undefined};},
}));
vi.mock('@/entities/video-template/api/videoTemplate.queries', () => ({
  useVideoTemplates: () => ({data: {items: mock.videoTemplates, total: mock.videoTemplates.length, pinnedId: null},
    isPending: false, error: null}),
}));
vi.mock('@/entities/template/api/replacement.queries', () => ({
  useReplacementCatalog: () => ({...mock.catalog, data: mock.catalogHidden ? undefined : mock.catalog.data}),
  usePrepareReplacement: () => ({ mutateAsync: mock.prepare }),
  useExecuteReplacement: () => ({ mutateAsync: mock.execute, isPending: false }),
  useReviewReplacement: () => ({mutateAsync: mock.review, isPending: false}),
  useManageReplacement: () => ({mutateAsync: mock.manage, isPending: false, reset: vi.fn()}),
}));
vi.mock('@/shared/lib/toast', () => ({ toast: vi.fn() }));

function Location() { return <output aria-label="地址">{useLocation().search}</output>; }
interface AppProps { url?: string; permissions?: string[] }
function App({ url = '/templates', permissions = ['*'] }: AppProps) {
  return <MemoryRouter initialEntries={[url]}><AccessProvider subject={{id: 'u_test', name: '测试', roles: [], permissions}}><ReplacementTemplates /><Location /></AccessProvider></MemoryRouter>;
}
function run(status: string): ReplacementRun {
  return { id: 'run1', kind: 'character', target: '测试人物', status, archivePath: 'Z:/runs/run1',
    createdAt: '2026-11-05T00:00:00Z', prompt: '最终提示词', taskId: 'existing', progress: 50,
    reviewStatus: 'pending' };
}

beforeEach(() => {
  mock.prepare.mockReset();
  mock.execute.mockReset();
  mock.manage.mockReset();
  mock.mediaUrls.length = 0;
  mock.videoTemplates = [];
  mock.catalog.data.runs = [];
  mock.catalog.data.channels = undefined;
  mock.catalog.data.packs = [
    {id: 'character-pack', kind: 'character', name: '测试人物', referenceIds: ['image'], coverReferenceId: 'image'},
    {id: 'other-character', kind: 'character', name: '另一人物', referenceIds: ['image'], coverReferenceId: 'image'},
    {id: 'product-pack', kind: 'product', name: '测试产品', referenceIds: ['product-back', 'product-front', 'product-detail'], coverReferenceId: 'product-front', supportedScopes: ['worn', 'held', 'boxed', 'screen']},
  ];
  mock.assetsLoading = false;
  mock.assetsError = null;
  mock.assetsHidden = false;
  mock.assetsEmpty = false;
  mock.catalogHidden = false;
  mock.catalog.error = null;
  mock.catalog.isLoading = false;
  mock.catalog.isFetching = false;
  mock.assetsRefetch.mockReset();
  mock.catalog.refetch.mockReset();
  mock.upload.mockReset();
  mock.uploadPending = false;
});

describe('替换模板工作台', () => {
  it('单条和批量准备保存所选渠道，换渠道使用独立请求', async () => {
    mock.catalog.data.channels = [
      {id: 'libtv', label: 'LibTV', model: 'seedance', enabled: true, billing: 'libtv'},
      {id: 'bailian', label: '百炼', model: 'wan', enabled: true, billing: 'aliyun'},
      {id: 'bailian-prime', label: '百炼 Prime', model: 'wan-prime', enabled: false, billing: 'aliyun', unavailableReason: '尚未配置'},
    ];
    mock.prepare.mockResolvedValue({...run('prepared'), taskId: null});
    render(<App url='/templates?sourceId=source&packId=character-pack' />);
    const selector = screen.getByRole('combobox', {name: '模型渠道'});
    expect(within(selector).getByRole('option', {name: /百炼 Prime/})).toBeDisabled();
    fireEvent.change(selector, {target: {value: 'bailian'}});
    expect(screen.getByText('核对后再生成，使用阿里云 API 计费。')).toBeVisible();
    fireEvent.click(screen.getByRole('button', {name: '下一步：确认替换'}));
    await vi.waitFor(() => expect(mock.prepare).toHaveBeenCalledWith(expect.objectContaining({channel: 'bailian'})));
    const firstRequest = mock.prepare.mock.calls[0][0].requestId;
    fireEvent.change(selector, {target: {value: 'libtv'}});
    fireEvent.click(screen.getByRole('button', {name: '下一步：确认替换'}));
    await vi.waitFor(() => expect(mock.prepare).toHaveBeenCalledTimes(2));
    expect(mock.prepare.mock.calls[1][0]).toMatchObject({channel: 'libtv'});
    expect(mock.prepare.mock.calls[1][0].requestId).not.toBe(firstRequest);
    fireEvent.change(selector, {target: {value: 'bailian'}});
    fireEvent.click(screen.getByText('批量准备多条原片'));
    fireEvent.click(screen.getByRole('checkbox', {name: '测试原片'}));
    fireEvent.click(screen.getByRole('button', {name: '准备所选原片／继续未完成项'}));
    await vi.waitFor(() => expect(mock.prepare).toHaveBeenCalledTimes(3));
    expect(mock.prepare.mock.calls[2][0]).toMatchObject({channel: 'bailian'});
  });

  it('LibTV 在执行期才注册 provider 时不被前端门禁禁用，只有付费渠道按目录判可用', async () => {
    mock.catalog.data.channels = [
      {id: 'libtv', label: 'LibTV', model: 'libtv-wan3-replacement', enabled: false, billing: 'libtv', unavailableReason: '服务端未启用此渠道'},
      {id: 'bailian', label: '百炼', model: 'wan', enabled: false, billing: 'aliyun', unavailableReason: '服务端尚未配置百炼密钥'},
    ];
    mock.prepare.mockResolvedValue({...run('prepared'), taskId: null});
    render(<App url='/templates?sourceId=source&packId=character-pack' />);
    const selector = screen.getByRole('combobox', {name: '模型渠道'});
    const next = screen.getByRole('button', {name: '下一步：确认替换'});
    /* 后端 `require_channel` 对 libtv 无条件放行（允许“执行期才注册”的安装），
       前端若照抄目录里的 enabled 就会让这类部署整站点不了「下一步」。 */
    expect(next).toBeEnabled();

    /* 付费渠道仍然按目录判可用：它没有“执行期注册”这条兼容路径。 */
    fireEvent.change(selector, {target: {value: 'bailian'}});
    expect(screen.getByRole('button', {name: '下一步：确认替换'})).toBeDisabled();

    fireEvent.change(selector, {target: {value: 'libtv'}});
    fireEvent.click(screen.getByRole('button', {name: '下一步：确认替换'}));
    await vi.waitFor(() => expect(mock.prepare).toHaveBeenCalledTimes(1));
  });

  it.each(['bailian', 'bailian-prime'] as const)('核对重试沿用已保存的 %s 渠道及阿里云计费', async channel => {
    mock.catalog.data.runs = [{...run('failed'), taskId: null, channel, channelLabel: '已保存百炼渠道'}];
    mock.execute.mockResolvedValue({...run('queued'), taskId: null, channel});
    render(<App url='/templates?channel=libtv' />);
    fireEvent.click(screen.getByRole('button', {name: '重试'}));
    const dialog = await screen.findByRole('dialog', {name: '核对替换任务'});
    expect(dialog).toHaveTextContent('已保存百炼渠道');
    expect(dialog).toHaveTextContent('阿里云 API 计费');
    expect(within(dialog).queryByRole('checkbox', {name: '用积分加速生成'})).not.toBeInTheDocument();
    expect(dialog).not.toHaveTextContent('0 积分');
    fireEvent.click(within(dialog).getByRole('button', {name: '确认生成'}));
    await vi.waitFor(() => expect(mock.execute).toHaveBeenCalledWith({id: 'run1', billingMode: undefined}));
    expect(mock.prepare).not.toHaveBeenCalled();
  });

  it('非 LibTV 档案显示保存渠道和阿里云计费，不显示积分加速', () => {
    mock.catalog.data.runs = [{...run('waiting_free'), taskId: null, channel: 'bailian', channelLabel: '百炼已保存'}];
    render(<App url='/templates?run=run1' />);
    const dialog = screen.getByRole('dialog', {name: '调用档案'});
    expect(dialog).toHaveTextContent('百炼已保存');
    expect(dialog).toHaveTextContent('阿里云 API 计费');
    expect(dialog).not.toHaveTextContent('零积分');
    expect(screen.queryByRole('button', {name: '用积分加速生成'})).not.toBeInTheDocument();
  });
  it('只选手持后切换产品及刷新目录，不扩大范围，核对任务沿用当次选择', async () => {
    mock.catalog.data.packs.push({...mock.catalog.data.packs[2], id: 'other-product', name: '另一个产品', version: 2});
    mock.videoTemplates = [{id: 'scope-template', templateKey: 'scope-template', name: '产品范围模板',
      description: '', prompt: '编辑 {{Mixed 1}}，目标 {{Mixed 2}}。允许编辑的范围：{{ReplacementScope}}。',
      promptSha256: 'test', sourceVideoName: '米兰之夜.mp4', sourceVideoAssetId: 'source-template',
      sourceVideoPath: '', sourceDurationSec: 5.31, aspectRatio: '9:16', generation: {}, coverUrl: '', pinned: false,
      createdAt: '2026-11-10T00:00:00Z', updatedAt: '2026-11-10T00:00:00Z', packId: 'product-pack', defaultKind: 'product',
      rules: {scopeOptions: ['人物上身穿着', '手持展示产品', '盒内可见产品', '手机详情页产品图']}, mediaOrder: []}];
    mock.prepare.mockImplementation(async (input: {replacementScope: string}) => {
      const result: ReplacementRun = {...run('prepared'), kind: 'product', prompt: `允许编辑的范围：${input.replacementScope}。`};
      mock.catalog.data.runs = [result];
      return result;
    });
    const view = render(<App url='/templates?sourceId=source-template&videoTemplateId=scope-template&kind=product&packId=product-pack' />);
    const scopes = within(screen.getByRole('group', {name: '原片中允许替换的位置'})).getAllByRole<HTMLInputElement>('checkbox');
    for (const checkbox of scopes) if (checkbox.checked) fireEvent.click(checkbox);
    fireEvent.click(screen.getByRole('checkbox', {name: '手持展示产品'}));
    fireEvent.click(screen.getByRole('button', {name: '选择产品：另一个产品'}));
    mock.catalog.data.packs = structuredClone(mock.catalog.data.packs);
    mock.videoTemplates = structuredClone(mock.videoTemplates);
    view.rerender(<App url='/templates?sourceId=source-template&videoTemplateId=scope-template&kind=product&packId=product-pack' />);
    expect(screen.getByRole('checkbox', {name: '手持展示产品'})).toBeChecked();
    for (const name of ['人物上身穿着', '盒内可见产品', '手机详情页产品图']) expect(screen.getByRole('checkbox', {name})).not.toBeChecked();
    fireEvent.click(screen.getByRole('button', {name: '下一步：确认替换'}));
    await vi.waitFor(() => expect(mock.prepare).toHaveBeenCalledWith(expect.objectContaining({replacementScope: '手持展示产品', packId: 'other-product'})));
    const review = await screen.findByRole('dialog', {name: '核对替换任务'});
    expect(within(review).getAllByText('允许编辑的范围：手持展示产品。').length).toBeGreaterThan(0);
  });

  it('文字换场景只编辑描述，空白不可提交，轮询不覆盖编辑并可恢复默认', async () => {
    const preset = '将原视频场景替换为明亮雅致的高档酒店会客厅。';
    mock.videoTemplates = [{id: 'scene-text', name: '文字换场景', defaultKind: 'scene', sourceVideoAssetId: 'source-template',
      templateKey: 'scene-text', description: '', promptSha256: 'test', sourceVideoName: '米兰之夜.mp4',
      sourceVideoPath: '', sourceDurationSec: 5.31, aspectRatio: '9:16', generation: {}, coverUrl: '', pinned: false,
      createdAt: '2026-11-10T00:00:00Z', updatedAt: '2026-11-10T00:00:00Z',
      rules: {inputMode: 'video_scene_text', scenePromptDefault: preset, sourceVideoAssetId: 'source-template', functionLabel: '文字换场景'},
      prompt: '{{ScenePrompt}}\n\n保留人物和产品。', mediaOrder: [{token: '{{Mixed 1}}', type: 'video', role: 'source_video', inPrompt: false}],
    }];
    mock.prepare.mockResolvedValue(run('prepared'));
    const view = render(<App url='/templates?sourceId=source-template&kind=scene&videoTemplateId=scene-text' />);
    const field = screen.getByRole('textbox', {name: '场景描述'});
    expect(field).toHaveValue(preset);
    expect(screen.queryByText('选择目标场景')).not.toBeInTheDocument();
    fireEvent.change(field, {target: {value: '   '}});
    expect(screen.getByRole('button', {name: '下一步：确认替换'})).toBeDisabled();
    fireEvent.change(field, {target: {value: '将场景替换为海边花园。'}});
    mock.videoTemplates = structuredClone(mock.videoTemplates);
    view.rerender(<App url='/templates?sourceId=source-template&kind=scene&videoTemplateId=scene-text' />);
    expect(field).toHaveValue('将场景替换为海边花园。');
    fireEvent.click(screen.getByRole('button', {name: '下一步：确认替换'}));
    await vi.waitFor(() => expect(mock.prepare).toHaveBeenCalledOnce());
    expect(mock.prepare).toHaveBeenCalledWith(expect.objectContaining({kind: 'scene', videoTemplateId: 'scene-text',
      scenePrompt: '将场景替换为海边花园。', packId: null, packVersion: null, referenceIds: [], notes: ''}));
    await vi.waitFor(() => expect(screen.getByRole('button', {name: '恢复默认场景描述'})).toBeEnabled());
    fireEvent.click(screen.getByRole('button', {name: '恢复默认场景描述'}));
    expect(field).toHaveValue(preset);
  });

  it.each(['failed', 'timeout', 'service_error', 'needs_login'])('列表中 %s 任务可重试原查询，保留任务和计费模式', async status => {
    mock.catalog.data.runs = [{...run(status), billingMode: 'credits', lastError: '查询中断'}];
    mock.execute.mockResolvedValue({...run('running'), billingMode: 'credits'});
    render(<App />);
    fireEvent.click(screen.getByRole('button', {name: '重试'}));
    await vi.waitFor(() => expect(mock.execute).toHaveBeenCalledOnce());
    expect(mock.execute).toHaveBeenCalledWith({id: 'run1', billingMode: undefined});
    expect(mock.prepare).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog', {name: '核对替换任务'})).not.toBeInTheDocument();
  });

  it('失败档案详情中可以重试已有任务', async () => {
    mock.catalog.data.runs = [run('failed')];
    mock.execute.mockResolvedValue(run('running'));
    render(<App url='/templates?run=run1' />);
    const dialog = screen.getByRole('dialog', {name: '调用档案'});
    expect(within(dialog).getByText('只恢复查询已有上游任务，不重复提交或扣费。')).toBeVisible();
    fireEvent.click(within(dialog).getByRole('button', {name: '重试'}));
    await vi.waitFor(() => expect(mock.execute).toHaveBeenCalledWith({id: 'run1', billingMode: undefined}));
  });

  it.each(['free', 'credits'] as const)('未提交的失败任务先核对，再以 %s 模式重试', async billingMode => {
    mock.catalog.data.runs = [{...run('failed'), taskId: null, billingMode}];
    mock.execute.mockResolvedValue({...run('queued'), taskId: null, billingMode});
    render(<App url='/templates?run=run1' />);
    fireEvent.click(within(screen.getByRole('dialog', {name: '调用档案'})).getByRole('button', {name: '重试'}));
    const review = screen.getByRole('dialog', {name: '核对替换任务'});
    expect(screen.queryByRole('dialog', {name: '调用档案'})).not.toBeInTheDocument();
    expect(mock.execute).not.toHaveBeenCalled();
    expect(within(review).getByRole('checkbox', {name: '用积分加速生成'})).toHaveProperty('checked', billingMode === 'credits');
    fireEvent.click(within(review).getByRole('button', {name: billingMode === 'credits' ? '用积分加速' : '确认生成'}));
    await vi.waitFor(() => expect(mock.execute).toHaveBeenCalledWith({id: 'run1', billingMode}));
    expect(mock.prepare).not.toHaveBeenCalled();
  });

  it.each(['success', 'running', 'submission_unknown', 'prepare_failed'])('%s 任务不显示失败重试按钮', status => {
    mock.catalog.data.runs = [run(status)];
    render(<App url='/templates?run=run1' />);
    expect(screen.queryByRole('button', {name: '重试'})).not.toBeInTheDocument();
  });

  it('只读用户和回收站任务没有重试按钮', () => {
    mock.catalog.data.runs = [run('failed')];
    const view = render(<App url='/templates?run=run1' permissions={['template:read', 'asset:read']} />);
    expect(screen.queryByRole('button', {name: '重试'})).not.toBeInTheDocument();
    view.unmount();
    mock.catalog.data.runs = [{...run('failed'), deletedAt: '2026-11-10T00:00:00Z'}];
    render(<App url='/templates?run=run1&trash=1' />);
    expect(screen.queryByRole('button', {name: '重试'})).not.toBeInTheDocument();
  });

  it('重试请求中禁用重复点击，失败后展示原因并允许再次重试', async () => {
    mock.catalog.data.runs = [run('failed')];
    let rejectRequest!: (error: Error) => void;
    mock.execute.mockImplementationOnce(() => new Promise((_resolve, reject) => {rejectRequest = reject;}));
    render(<App />);
    const retry = screen.getByRole('button', {name: '重试'});
    fireEvent.click(retry);
    expect(retry).toBeDisabled();
    fireEvent.click(retry);
    expect(mock.execute).toHaveBeenCalledOnce();
    await act(async () => {rejectRequest(new Error('执行器仍不可用'));});
    expect(screen.getByRole('alert')).toHaveTextContent('执行器仍不可用');
    expect(retry).toBeEnabled();
    mock.execute.mockResolvedValue(run('running'));
    fireEvent.click(retry);
    await vi.waitFor(() => expect(mock.execute).toHaveBeenCalledTimes(2));
  });

  it('重试响应仍是失败时展示实际原因，不误报已恢复', async () => {
    mock.catalog.data.runs = [run('failed')];
    mock.execute.mockResolvedValue({...run('failed'), lastError: '安全守卫仍阻止清理'});
    render(<App />);
    fireEvent.click(screen.getByRole('button', {name: '重试'}));
    expect(await screen.findByRole('alert')).toHaveTextContent('安全守卫仍阻止清理');
    expect(screen.queryByText(/已处理任务的执行请求/)).not.toBeInTheDocument();
  });

  it('批量准备首错暂停，继续时不重复成功项且失败项沿用请求标识', async () => {
    mock.prepare.mockResolvedValueOnce({...run('prepared'), id: 'batch-first'})
      .mockRejectedValueOnce(new Error('暂时断连'))
      .mockResolvedValueOnce({...run('prepared'), id: 'batch-second'});
    render(<App />);
    fireEvent.click(screen.getByRole('button', {name: '选择模板：测试原片'}));
    fireEvent.click(screen.getByRole('button', {name: '选择人物：测试人物'}));
    fireEvent.click(screen.getByText('批量准备多条原片'));
    fireEvent.click(screen.getByRole('checkbox', {name: '测试原片'}));
    fireEvent.click(screen.getByRole('checkbox', {name: '十五秒原片'}));
    const start = screen.getByRole('button', {name: '准备所选原片／继续未完成项'});
    fireEvent.click(start);
    await vi.waitFor(() => expect(mock.prepare).toHaveBeenCalledTimes(2));
    expect(await screen.findByText('暂时断连')).toBeVisible();
    const failedRequest = mock.prepare.mock.calls[1][0];
    await vi.waitFor(() => expect(start).toBeEnabled());
    fireEvent.click(start);
    await vi.waitFor(() => expect(mock.prepare).toHaveBeenCalledTimes(3));
    expect(mock.prepare.mock.calls[2][0].requestId).toBe(failedRequest.requestId);
    expect(mock.prepare.mock.calls.map(call => call[0].sourceId)).toEqual(['source', 'source-15s', 'source-15s']);
    expect(mock.execute).not.toHaveBeenCalled();
  });
  it('从任务中心的深链接进入后自动打开对应档案，关闭时清理 URL', () => {
    mock.catalog.data.runs = [run('running')];
    render(<App url='/templates?kind=character&run=run1' />);
    expect(screen.getByRole('dialog', {name: '调用档案'})).toBeVisible();
    expect(screen.getByRole('dialog', {name: '调用档案'})).toHaveTextContent('run1');
    fireEvent.click(within(screen.getByRole('dialog', {name: '调用档案'})).getByRole('button', {name: '关闭'}));
    expect(screen.queryByRole('dialog', {name: '调用档案'})).not.toBeInTheDocument();
    expect(screen.getByLabelText('地址')).not.toHaveTextContent('run=');
  });

  it('深链接任务不属于当前目录时明确提示找不到', () => {
    render(<App url='/templates?run=missing' />);
    expect(screen.getByRole('alert')).toHaveTextContent('未找到此替换档案');
  });

  it('同一目标的两次替换显示不同编号，打开和删除操作指向对应任务', () => {
    const firstId = 'aaaaaaaa111111111111111111111111';
    const secondId = 'bbbbbbbb222222222222222222222222';
    mock.catalog.data.runs = [
      {...run('prepared'), id: firstId, taskId: null},
      {...run('prepared'), id: secondId, taskId: null},
    ];
    render(<App />);
    expect(screen.getByRole('button', {name: /测试人物 · #AAAAAAAA.*查看档案/})).toBeVisible();
    fireEvent.click(screen.getByRole('button', {name: /测试人物 · #BBBBBBBB.*查看档案/}));
    const dialog = screen.getByRole('dialog', {name: '调用档案'});
    expect(within(dialog).getByText(new RegExp(`任务编号：${secondId}`))).toBeVisible();
    expect(within(dialog).getByText(/^原片：历史档案未记录原片$/)).toBeVisible();
    fireEvent.click(within(dialog).getByRole('button', {name: '移入回收站'}));
    expect(within(dialog).getByText(/将“历史档案未记录原片 · 人物替换 · 测试人物 · #BBBBBBBB”移入回收站/)).toBeVisible();
    fireEvent.click(within(dialog).getByRole('button', {name: '取消'}));
    fireEvent.click(within(dialog).getByRole('button', {name: '核对替换任务'}));
    expect(screen.getByRole('dialog', {name: '核对替换任务'})).toHaveTextContent(secondId);
    expect(mock.execute).not.toHaveBeenCalled();
  });
  it('列表与档案详情显示耗时，历史档案明确标「未记录」', () => {
    mock.catalog.data.runs = [
      {...run('success'), startedAt: '2026-11-05T00:00:00Z', finishedAt: '2026-11-05T00:02:05Z', elapsedSec: 125},
      {...run('failed'), id: 'legacy', target: '历史人物'},
    ];
    render(<App />);
    expect(screen.getByText(/耗时：2 分 5 秒/)).toBeVisible();
    fireEvent.click(screen.getByRole('button', {name: /历史人物.*查看档案/}));
    const dialog = screen.getByRole('dialog', {name: '调用档案'});
    // 上线前的档案没有可信提交时刻：不拿创建时间冒充
    expect(within(dialog).getByText(/耗时：未记录/)).toBeVisible();
  });
  it('档案里先给「本次实际提交的提示词」，归档路径明确标成排查用', () => {
    /* 两行以前挨着、路径在上，读起来就像「提示词 = 那个 runs 目录」。
       封装模板跑出来的任务还要自报来源，用户才敢相信跑的是自己写的那份。 */
    mock.catalog.data.runs = [{
      ...run('success'),
      prompt: '固定提示词正文',
      promptMode: 'fixed',
      videoTemplateName: '三人高端姐 · 三人同款内衣替换',
    }];
    render(<App />);
    fireEvent.click(screen.getByRole('button', {name: /测试人物.*查看档案/}));
    const dialog = screen.getByRole('dialog', {name: '调用档案'});
    fireEvent.click(within(dialog).getByText('查看最终提示词与归档路径'));
    expect(within(dialog).getByText('最终提示词（本次实际提交）')).toBeVisible();
    expect(within(dialog).getByText(/来源：视频模板「三人高端姐 · 三人同款内衣替换」的固定提示词，逐字提交，未改写/)).toBeVisible();
    expect(within(dialog).getByText(/归档路径（排查用，非本次提示词）：Z:\/runs\/run1/)).toBeVisible();
  });

  it('任务记录给固定提示词任务打标记，现编提示词的不打', () => {
    mock.catalog.data.runs = [
      {...run('success'), promptMode: 'fixed', videoTemplateName: '三人高端姐'},
      {...run('success'), id: 'compiled-run', target: '现编人物'},
    ];
    render(<App />);
    expect(screen.getAllByText('固定提示词')).toHaveLength(1);
    expect(screen.getByTitle('固定提示词：三人高端姐')).toBeVisible();
  });

  it('已准备任务从档案重新进入核对，不直接提交生成', () => {
    mock.catalog.data.runs = [{...run('prepared'), taskId: null}];
    render(<App />);
    fireEvent.click(screen.getByRole('button', {name: /测试人物.*查看档案/}));
    fireEvent.click(screen.getByRole('button', {name: '核对替换任务'}));
    expect(screen.getByRole('dialog', {name: '核对替换任务'})).toBeVisible();
    expect(screen.getByRole('button', {name: '返回调整'})).toBeVisible();
    expect(mock.execute).not.toHaveBeenCalled();
  });
  it('任务记录支持搜索、状态筛选和回收站，恢复后返回任务记录', async () => {
    mock.catalog.data.runs = [run('prepared'), {...run('failed'), id: 'deleted', target: '历史人物', deletedAt: '2026-11-06T00:00:00Z'}];
    mock.manage.mockResolvedValue({...run('failed'), id: 'deleted', deletedAt: null});
    render(<App />);
    expect(screen.getByRole('button', {name: /测试人物.*查看档案/})).toBeVisible();
    expect(screen.queryByRole('button', {name: /历史人物.*查看档案/})).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox', {name: '搜索替换任务'}), {target: {value: '不匹配'}});
    expect(screen.getByText('没有匹配的任务')).toBeVisible();
    fireEvent.change(screen.getByRole('textbox', {name: '搜索替换任务'}), {target: {value: ''}});
    fireEvent.change(screen.getByRole('combobox', {name: '筛选替换任务状态'}), {target: {value: 'prepared'}});
    expect(screen.getByRole('button', {name: /测试人物.*查看档案/})).toBeVisible();
    fireEvent.click(screen.getByRole('button', {name: '回收站（1）'}));
    expect(screen.getByRole('button', {name: /历史人物.*查看档案/})).toBeVisible();
    fireEvent.click(screen.getByRole('button', {name: '恢复任务'}));
    await vi.waitFor(() => expect(mock.manage).toHaveBeenCalledWith({id: 'deleted', restore: true}));
  });

  it('移入回收站需确认，失败保留记录且只读用户没有管理按钮', async () => {
    mock.catalog.data.runs = [run('running')];
    mock.manage.mockRejectedValue(new Error('共享盘不可用'));
    const view = render(<App />);
    fireEvent.click(screen.getByRole('button', {name: '移入回收站'}));
    expect(screen.getByText(/正在执行的生成会继续/)).toBeVisible();
    expect(mock.manage).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', {name: '取消'}));
    expect(screen.queryByRole('button', {name: '确认移入回收站'})).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name: '移入回收站'}));
    fireEvent.click(screen.getByRole('button', {name: '确认移入回收站'}));
    await vi.waitFor(() => expect(mock.manage).toHaveBeenCalledWith({id: 'run1', restore: false}));
    expect(await screen.findByRole('alert')).toHaveTextContent('共享盘不可用');
    expect(screen.getByRole('button', {name: /测试人物.*查看档案/})).toBeVisible();
    view.unmount();
    render(<App permissions={['template:read', 'asset:read']} />);
    expect(screen.queryByRole('button', {name: '移入回收站'})).not.toBeInTheDocument();
  });
  it('产品只展示正面封面，准备任务仍携带完整素材包的全部视角', async () => {
    mock.prepare.mockResolvedValue({...run('prepared'), kind: 'product', target: '测试产品'});
    render(<App url="/templates?kind=product" />);
    fireEvent.click(screen.getByRole('button', {name: '选择模板：测试原片'}));
    expect(screen.getByRole('button', {name: '下一步：确认替换'})).toBeDisabled();
    expect(mock.mediaUrls).toContain('/thumbnail/product-front?v=hd-v2');
    expect(mock.mediaUrls).not.toContain('/thumbnail/product-back?v=hd-v2');
    expect(mock.mediaUrls).not.toContain('/thumbnail/product-detail?v=hd-v2');
    fireEvent.click(screen.getByRole('button', {name: '选择产品：测试产品'}));
    fireEvent.click(screen.getByRole('checkbox', {name: '手持展示产品'}));
    expect(screen.queryByRole('combobox', {name: '已封装素材包'})).not.toBeInTheDocument();
    expect(screen.queryByRole('textbox', {name: '目标名称'})).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name: '下一步：确认替换'}));
    await vi.waitFor(() => expect(mock.prepare).toHaveBeenCalledOnce());
    expect(mock.prepare.mock.calls[0][0]).toMatchObject({packId: 'product-pack', target: '测试产品',
      referenceIds: ['product-back', 'product-front', 'product-detail']});
    expect(mock.execute).not.toHaveBeenCalled();
  });

  it('没有素材包时显示准备引导，不回退为手动选图', () => {
    mock.catalog.data.packs = [];
    render(<App url="/templates?kind=product" />);
    fireEvent.click(screen.getByRole('button', {name: '选择模板：测试原片'}));
    expect(screen.getByText('暂无可用的产品素材包')).toBeVisible();
    expect(screen.getByRole('button', {name: '下一步：确认替换'})).toBeDisabled();
    expect(screen.queryByRole('button', {name: '上传参考图'})).not.toBeInTheDocument();
  });

  it('素材包不完整时不可选，切换类型会清除原选择', () => {
    mock.catalog.data.packs.push({id: 'broken', kind: 'character', name: '缺失素材', referenceIds: ['not-found']});
    render(<App />);
    fireEvent.click(screen.getByRole('button', {name: '选择模板：测试原片'}));
    expect(screen.getByRole('button', {name: '选择人物：缺失素材'})).toBeDisabled();
    fireEvent.click(screen.getByRole('button', {name: '选择人物：测试人物'}));
    expect(screen.getByRole('button', {name: '下一步：确认替换'})).toBeEnabled();
    fireEvent.click(screen.getByRole('button', {name: '产品替换'}));
    expect(screen.getByRole('button', {name: '下一步：确认替换'})).toBeDisabled();
    fireEvent.click(screen.getByRole('button', {name: '人物替换'}));
    expect(screen.getByRole('button', {name: '选择人物：测试人物'})).toHaveAttribute('aria-pressed', 'false');
  });

  it('成功状态但结果文件缺失时不显示成片成功反馈或评价操作', () => {
    mock.catalog.data.runs = [run('success')];
    render(<App />);
    fireEvent.click(screen.getByRole('button', {name: /测试人物.*结果文件待恢复/}));
    expect(screen.getByText(/文件恢复前无法查看成片和评价/)).toBeVisible();
    expect(screen.queryByRole('button', {name: '评价成片'})).not.toBeInTheDocument();
    expect(screen.queryByText(/成片已生成并保存，可播放和下载/)).not.toBeInTheDocument();
  });
  it('核对实际归档输入，可移除当前原片后重新选择', async () => {
    const prepared = {...run('prepared'), taskId: null, inputSummary: {
      assets: [{id: 'original', name: '归档原片.mp4', role: 'source_video', type: 'video', url: '/snapshot/video'},
        {id: 'reference', name: '归档人物.png', role: 'identity', type: 'image', url: '/snapshot/image'}],
      wardrobeMode: 'preserve', notes: '保留原动作',
    }};
    mock.catalog.data.runs = [prepared];
    mock.prepare.mockResolvedValue(prepared);
    render(<App />);
    fireEvent.click(screen.getByRole('button', {name: '选择模板：测试原片'}));
    fireEvent.click(screen.getByRole('button', {name: '选择模板：第三原片'}));
    fireEvent.click(screen.getByText('查看已选原片'));
    fireEvent.click(screen.getByRole('button', {name: '移除原片：第三原片'}));
    expect(screen.getByRole('button', {name: '选择模板：第三原片'})).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(screen.getByRole('button', {name: '选择模板：测试原片'}));
    fireEvent.click(screen.getByRole('button', {name: '选择人物：测试人物'}));
    fireEvent.click(screen.getByRole('button', {name: '下一步：确认替换'}));
    const dialog = await screen.findByRole('dialog');
    expect(mock.mediaUrls).toContain('/snapshot/image');
    expect(within(dialog).getByText('归档人物.png')).toBeVisible();
    expect(within(within(dialog).getByLabelText('当次完整输入')).getByText('归档原片.mp4')).toBeVisible();
    expect(within(dialog).getByText(/保留原动作/)).toBeVisible();
    expect(mock.execute).not.toHaveBeenCalled();
  });

  it('只能选择一条原片，切换后仅准备当前视频，准备失败复用请求标识', async () => {
    mock.prepare.mockRejectedValueOnce(new Error('断连')).mockResolvedValueOnce({...run('prepared'), taskId: null});
    mock.catalog.data.runs = [{...run('prepared'), taskId: null}];
    render(<App />);
    for (const name of ['测试原片', '十五秒原片', '第三原片']) fireEvent.click(screen.getByRole('button', {name: `选择模板：${name}`}));
    expect(screen.getByRole('button', {name: '选择模板：测试原片'})).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByRole('button', {name: '选择模板：十五秒原片'})).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByRole('button', {name: '选择模板：第三原片'})).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByText(/支持批量/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name: '选择人物：测试人物'}));
    fireEvent.click(screen.getByRole('button', {name: '下一步：确认替换'}));
    await vi.waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('准备失败：断连'));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name: '下一步：确认替换'}));
    await screen.findByRole('dialog', {name: '核对替换任务'});
    expect(mock.prepare).toHaveBeenCalledTimes(2);
    expect(mock.prepare.mock.calls.map(args => args[0].sourceId)).toEqual(['source-third', 'source-third']);
    expect(mock.prepare.mock.calls[0][0].requestId).toBe(mock.prepare.mock.calls[1][0].requestId);
  });

  it('保留旧数据时显示更新错误，不清空已有素材和任务', () => {
    mock.assetsError = new Error('更新失败');
    mock.catalog.error = new Error('更新失败');
    mock.catalog.data.runs = [run('running')];
    render(<App />);
    expect(screen.getByText('素材更新失败，以下为上次加载的内容')).toBeVisible();
    expect(screen.getByRole('button', {name: '选择模板：测试原片'})).toBeVisible();
    expect(screen.getByRole('button', {name: /测试人物.*生成中/})).toBeVisible();
    expect(screen.queryByText('还没有替换任务')).not.toBeInTheDocument();
  });

  it('真实权限门禁隐藏上传、准备和继续执行操作', () => {
    mock.catalog.data.runs = [run('prepared')];
    render(<App permissions={['template:read', 'asset:read']} />);
    expect(screen.queryByRole('button', {name: '上传原片'})).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name: '选择模板：测试原片'}));
    expect(screen.queryByRole('button', {name: '上传参考图'})).not.toBeInTheDocument();
    expect(screen.queryByRole('button', {name: '下一步：确认替换'})).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name: /测试人物.*待核对/}));
    expect(screen.queryByRole('button', {name: '继续查询已有任务'})).not.toBeInTheDocument();
  });

  it('有 task:read 时给出任务中心入口，指到任务中心替换筛选', () => {
    render(<App permissions={['template:read', 'task:read']} />);
    expect(screen.getByRole('link', {name: /任务中心/})).toHaveAttribute('href', '/tasks?source=replacement');
    expect(screen.getByText(/也可在任务中心统一跟踪进度/)).toBeVisible();
  });

  it('没有 task:read 时不提供任务中心跳转，避免落到必然 403 的页面', () => {
    render(<App permissions={['template:read']} />);
    expect(screen.queryByRole('link', {name: /任务中心/})).not.toBeInTheDocument();
    expect(screen.getByText(/也可在任务中心统一跟踪进度/)).toBeVisible();
  });

  it('只提供素材包主图选择，并关联产品范围提示', () => {
    render(<App />);
    fireEvent.click(screen.getByRole('button', {name: '选择模板：测试原片'}));
    expect(screen.queryByRole('textbox', {name: '目标名称'})).not.toBeInTheDocument();
    expect(screen.queryByRole('button', {name: '上传参考图'})).not.toBeInTheDocument();
    expect(screen.getByRole('region', {name: '目标人物'})).toHaveAccessibleDescription(/点击主图选择人物/);
    fireEvent.click(screen.getByRole('button', {name: '选择人物：测试人物'}));
    expect(screen.getByRole('button', {name: '选择人物：测试人物'})).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByRole('button', {name: '产品替换'}));
    fireEvent.click(screen.getByRole('button', {name: '选择产品：测试产品'}));
    const scopes = within(screen.getByRole('group', {name: '原片中允许替换的位置'})).getAllByRole('checkbox');
    expect(scopes.every(scope => !(scope as HTMLInputElement).checked)).toBe(true);
    expect(scopes[0]).toHaveAccessibleDescription('请选择至少一个产品替换位置');
  });

  it('准备中锁定原片和配置，重复点击不会再次准备', async () => {
    let finish: ((value: ReplacementRun) => void) | undefined;
    mock.prepare.mockImplementation(() => new Promise<ReplacementRun>(resolve => {finish = resolve;}));
    render(<App />);
    fireEvent.click(screen.getByRole('button', {name: '选择模板：测试原片'}));
    fireEvent.click(screen.getByRole('button', {name: '选择人物：测试人物'}));
    const prepare = screen.getByRole('button', {name: '下一步：确认替换'});
    fireEvent.click(prepare);
    expect(prepare).toBeDisabled();
    expect(screen.getByRole('button', {name: '清空选择'})).toBeDisabled();
    expect(screen.getByRole('button', {name: '选择模板：十五秒原片'})).toBeDisabled();
    expect(screen.getByRole('button', {name: '产品替换'})).toBeDisabled();
    fireEvent.click(prepare);
    expect(mock.prepare).toHaveBeenCalledOnce();
    await act(async () => {finish?.(run('prepared'));});
    expect(prepare).toBeEnabled();
  });

  it('上传成功显示已保存反馈，失败后允许重新选择同一文件', async () => {
    mock.prepare.mockResolvedValue({...run('prepared'), taskId: null});
    render(<App />);
    fireEvent.click(screen.getByRole('button', {name: '选择模板：测试原片'}));
    const input = screen.getByLabelText('替换模板上传素材');
    const file = new File(['video'], '原片.mp4', {type: 'video/mp4'});
    fireEvent.change(input, {target: {files: [file]}});
    await act(async () => {mock.upload.mock.calls[0][1].onError(new Error('文件不合规'));});
    expect(screen.getByRole('alert')).toHaveTextContent('素材上传失败：文件不合规');
    fireEvent.change(input, {target: {files: [file]}});
    expect(mock.upload).toHaveBeenCalledTimes(2);
    const asset: ArchivedAsset = {id: 'uploaded', name: '原片.mp4', category: 'source', type: 'video',
      url: '/file', previewUrl: null, duration: 2, hasAudio: true, width: 720, height: 1280,
      path: 'library/source.mp4', sha256: 'hash', sizeBytes: 5, mimeType: 'video/mp4', createdAt: '2026-11-06T00:00:00Z'};
    await act(async () => {mock.upload.mock.calls[1][1].onSuccess(asset);});
    expect(screen.getByText(/“原片.mp4”已保存到素材库/).closest('[role="status"]')).toBeInTheDocument();
    expect(screen.getByRole('button', {name: '选择模板：测试原片'})).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(screen.getByRole('button', {name: '选择人物：测试人物'}));
    fireEvent.click(screen.getByRole('button', {name: '下一步：确认替换'}));
    await vi.waitFor(() => expect(mock.prepare).toHaveBeenCalledOnce());
    expect(mock.prepare.mock.calls[0][0].sourceId).toBe('uploaded');
  });

  it('首次加载展示骨架，不把尚未读取的数据解释为空', () => {
    mock.assetsLoading = true; mock.assetsHidden = true;
    mock.catalog.isLoading = true; mock.catalogHidden = true;
    render(<App />);
    expect(screen.getByRole('status', {name: '正在加载原片模板'})).toBeVisible();
    expect(screen.getByRole('status', {name: '正在加载替换任务'})).toBeVisible();
    expect(screen.queryByText('添加你的第一条原片')).not.toBeInTheDocument();
    expect(screen.queryByText('还没有替换任务')).not.toBeInTheDocument();
  });

  it('素材和任务加载失败分别提供重试，不显示误导性的空状态', () => {
    mock.assetsError = new Error('断连'); mock.assetsHidden = true;
    mock.catalog.error = new Error('断连'); mock.catalogHidden = true;
    render(<App />);
    expect(screen.getByText('原片与参考素材加载失败')).toBeVisible();
    expect(screen.getByText('任务与素材包加载失败')).toBeVisible();
    const retries = screen.getAllByRole('button', {name: '重试'});
    fireEvent.click(retries[0]); fireEvent.click(retries[1]);
    expect(mock.assetsRefetch).toHaveBeenCalledOnce();
    expect(mock.catalog.refetch).toHaveBeenCalledOnce();
    expect(screen.queryByText('添加你的第一条原片')).not.toBeInTheDocument();
    expect(screen.queryByText('还没有替换任务')).not.toBeInTheDocument();
  });

  it('空库提供上传引导，搜索无结果可以清空并恢复正常列表', () => {
    mock.assetsEmpty = true;
    const view = render(<App />);
    expect(screen.getByRole('button', {name: '上传第一条原片'})).toBeVisible();
    expect(screen.getByText('还没有替换任务')).toBeVisible();
    mock.assetsEmpty = false;
    fireEvent.change(screen.getByRole('searchbox'), {target: {value: '不存在'}});
    view.rerender(<App />);
    expect(screen.getByText('没有找到相关模板')).toBeVisible();
    fireEvent.click(screen.getByRole('button', {name: '清空搜索'}));
    expect(screen.getByRole('button', {name: '选择模板：测试原片'})).toBeVisible();
  });

  it('执行反馈区分请求已处理与生成完成，并可关闭', async () => {
    mock.catalog.data.runs = [run('running')];
    mock.execute.mockResolvedValue(run('running'));
    render(<App />);
    fireEvent.click(screen.getByRole('button', {name: /测试人物.*生成中/}));
    fireEvent.click(screen.getByRole('button', {name: '继续查询已有任务'}));
    /* 「继续查询已有任务」只查询不重提：反馈要说清「已在执行中」，否则用户会以为
       又排了一条新任务，或者以为按钮没生效。 */
    expect(await screen.findByText(/任务已在执行中/)).toBeInTheDocument();
    expect(screen.queryByText(/成片已生成并保存/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name: '关闭操作反馈'}));
    expect(screen.queryByText(/任务已在执行中/)).not.toBeInTheDocument();
  });

  it('单任务执行失败在核对弹窗保留错误，再次确认沿用同一任务', async () => {
    const prepared = {...run('prepared'), taskId: null};
    mock.catalog.data.runs = [prepared];
    mock.prepare.mockResolvedValue(prepared);
    mock.execute.mockRejectedValueOnce(new Error('连接中断')).mockResolvedValueOnce(run('running'));
    render(<App />);
    fireEvent.click(screen.getByRole('button', {name: '选择模板：测试原片'}));
    fireEvent.click(screen.getByRole('button', {name: '选择人物：测试人物'}));
    fireEvent.click(screen.getByRole('button', {name: '下一步：确认替换'}));
    const confirm = await screen.findByRole('button', {name: '确认生成'});
    fireEvent.click(confirm);
    await vi.waitFor(() => expect(within(screen.getByRole('dialog')).getByRole('alert')).toHaveTextContent('执行请求失败：连接中断'));
    fireEvent.click(confirm);
    await vi.waitFor(() => expect(mock.execute).toHaveBeenCalledTimes(2));
    expect(mock.execute.mock.calls.map(args => args[0])).toEqual([
      {id: 'run1', billingMode: 'free'}, {id: 'run1', billingMode: 'free'},
    ]);
    await vi.waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('核对时勾选积分加速，执行按 credits 提交', async () => {
    const prepared = {...run('prepared'), taskId: null};
    mock.catalog.data.runs = [prepared];
    mock.prepare.mockResolvedValue(prepared);
    mock.execute.mockResolvedValue(run('running'));
    render(<App />);
    fireEvent.click(screen.getByRole('button', {name: '选择模板：测试原片'}));
    fireEvent.click(screen.getByRole('button', {name: '选择人物：测试人物'}));
    fireEvent.click(screen.getByRole('button', {name: '下一步：确认替换'}));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByLabelText('用积分加速生成'));
    fireEvent.click(within(dialog).getByRole('button', {name: '用积分加速'}));
    await vi.waitFor(() => expect(mock.execute)
      .toHaveBeenCalledWith({id: 'run1', billingMode: 'credits'}));
  });

  it('已提交上游的任务不给加速勾选，只继续查询进度', async () => {
    /* 弹窗打开后任务在另一处被提交过（或状态已推进）时，勾加速只会拿到 409；
       这里把入口直接降级成查询，避免用户以为按钮坏了。 */
    const submitted = {...run('prepared'), taskId: 'up-1'};
    mock.catalog.data.runs = [submitted];
    mock.prepare.mockResolvedValue(submitted);
    mock.execute.mockResolvedValue(submitted);
    render(<App />);
    fireEvent.click(screen.getByRole('button', {name: '选择模板：测试原片'}));
    fireEvent.click(screen.getByRole('button', {name: '选择人物：测试人物'}));
    fireEvent.click(screen.getByRole('button', {name: '下一步：确认替换'}));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).queryByLabelText('用积分加速生成')).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', {name: '继续查询进度'}));
    await vi.waitFor(() => expect(mock.execute)
      .toHaveBeenCalledWith({id: 'run1', billingMode: undefined}));
  });

  it('等待免费队列的任务给出积分加速入口并带上当前报价', async () => {
    mock.catalog.data.runs = [{...run('waiting_free'), taskId: null, estimatedCredits: 40}];
    mock.execute.mockResolvedValue({...run('queued'), taskId: null, billingMode: 'credits'});
    render(<App />);
    fireEvent.click(await screen.findByRole('button', {name: '加速生成（约 40 积分）'}));
    fireEvent.click(screen.getByRole('button', {name: '确认用积分加速'}));
    await vi.waitFor(() => expect(mock.execute)
      .toHaveBeenCalledWith({id: 'run1', billingMode: 'credits'}));
  });

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
    expect(mock.mediaUrls).toContain('/api/v1/assets/source-15s/stream?v=hd-v2');
    expect(mock.mediaUrls).not.toContain('/api/v1/assets/source-15s/stream');
    expect(mock.mediaUrls).not.toContain('/api/v1/assets/source-15s/file');
  });

  it('准备失败后复用请求 ID，避免在新目录重复归档', async () => {
    mock.prepare.mockRejectedValue(new Error('模拟断连'));
    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: '选择模板：测试原片' }));
    fireEvent.click(screen.getByRole('button', {name: '选择人物：测试人物'}));
    const prepare = screen.getByRole('button', { name: '下一步：确认替换' });
    fireEvent.click(prepare);
    await screen.findByRole('button', { name: '下一步：确认替换' });
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
    expect(within(screen.getByRole('dialog')).getByText('历史档案未记录原片 · 人物替换 · 测试人物 · #RUN1 · 成片已保存')).toBeVisible();
    expect(screen.queryByRole('button', { name: '继续查询已有任务' })).not.toBeInTheDocument();
  });

  it('原片选择只写 URL：清空后地址里不再有 sourceId', () => {
    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: '选择模板：测试原片' }));
    expect(screen.getByLabelText('地址').textContent).toContain('sourceId=source');
    fireEvent.click(screen.getByRole('button', { name: '清空选择' }));
    expect(screen.getByLabelText('地址').textContent).not.toContain('sourceId');
    expect(screen.getByRole('button', { name: '选择模板：测试原片' })).toHaveAttribute('aria-pressed', 'false');
  });

  it('深链带入的原片直接成为唯一选择', () => {
    render(<App url="/templates?sourceId=source-third" />);
    expect(screen.getByRole('button', { name: '选择模板：第三原片' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: '选择模板：测试原片' })).toHaveAttribute('aria-pressed', 'false');
  });

  it('选中已封装的视频模板卡片：默认产品替换、带出绑定素材包、提示固定提示词生效', () => {
    mock.videoTemplates = [{
      id: 'tpl', templateKey: 'milan_night_3030_pink', name: '米兰之夜 · 3030 粉色',
      description: '', prompt: '固定提示词 {{Mixed 1}} {{Mixed 3}}', promptSha256: 'abc',
      sourceVideoName: '我穿的是米兰之夜模板测试.mp4', sourceVideoAssetId: 'source-template',
      sourceVideoPath: '', sourceDurationSec: 5.31, aspectRatio: '9:16',
      packId: 'product-pack', packName: '测试产品', packVersion: 1, defaultKind: 'product',
      mediaOrder: [], generation: {}, rules: {}, coverUrl: '', pinned: true,
      createdAt: '2026-11-09T07:00:00Z', updatedAt: '2026-11-09T07:00:00Z',
    }];
    render(<App />);
    expect(screen.getByText('视频模板 · 产品替换')).toBeVisible();
    fireEvent.click(screen.getByRole('button', {name: '查看提示词：我穿的是米兰之夜模板测试.mp4'}));
    expect(screen.getByRole('dialog', {name: '模板提示词与输入预览'})).toHaveTextContent('固定提示词');
    expect(mock.prepare).not.toHaveBeenCalled();
    expect(screen.getByRole('button', {name: '选择模板：我穿的是米兰之夜模板测试.mp4'})).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(within(screen.getByRole('dialog', {name: '模板提示词与输入预览'})).getByRole('button', {name: '关闭'}));
    fireEvent.click(screen.getByRole('button', {name: '选择模板：我穿的是米兰之夜模板测试.mp4'}));
    expect(screen.getByRole('button', {name: '产品替换'})).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByLabelText('地址').textContent).toContain('kind=product');
    expect(screen.getByLabelText('地址').textContent).toContain('packId=product-pack');
    expect(screen.getByText(/已套用视频模板「米兰之夜 · 3030 粉色」/)).toBeVisible();
  });

  it('封装只有产品替换时，不显示其他功能或未声明的范围参数', () => {
    mock.videoTemplates = [{
      id: 'tpl', templateKey: 'milan_night_3030_pink', name: '米兰之夜 · 3030 粉色',
      description: '', prompt: '固定提示词 {{Mixed 1}} {{Mixed 3}}', promptSha256: 'abc',
      sourceVideoName: '我穿的是米兰之夜模板测试.mp4', sourceVideoAssetId: 'source-template',
      sourceVideoPath: '', sourceDurationSec: 5.31, aspectRatio: '9:16',
      packId: 'product-pack', packName: '测试产品', packVersion: 1, defaultKind: 'product',
      mediaOrder: [], generation: {}, rules: {}, coverUrl: '', pinned: true,
      createdAt: '2026-11-09T07:00:00Z', updatedAt: '2026-11-09T07:00:00Z',
    }];
    render(<App />);
    fireEvent.click(screen.getByRole('button', {name: '选择模板：我穿的是米兰之夜模板测试.mp4'}));
    expect(screen.getByText(/已套用视频模板/)).toBeVisible();
    expect(screen.queryByRole('button', {name: '人物替换'})).not.toBeInTheDocument();
    expect(screen.queryByRole('button', {name: '场景替换'})).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', {name: '替换范围'})).not.toBeInTheDocument();
    expect(screen.getByRole('button', {name: '下一步：确认替换'})).toBeEnabled();
  });

  it.each([
    {kind: 'product' as const, short: '产品', action: '产品替换'},
    {kind: 'scene' as const, short: '场景', action: '场景替换'},
  ])('$short 始终单选，不显示人物序号或携带额外包', async ({kind, short, action}) => {
    mock.catalog.data.packs = ['a', 'b'].map(id => ({id, kind, name: `目标${id}`,
      referenceIds: [`image-${id}`], available: true, version: 1, supportedScopes: ['held']}));
    mock.prepare.mockResolvedValue({...run('prepared'), kind});
    render(<App />);
    fireEvent.click(screen.getByRole('button', {name: '选择模板：测试原片'}));
    fireEvent.click(screen.getByRole('button', {name: action}));
    const first = screen.getByRole('button', {name: `选择${short}：目标a`});
    const second = screen.getByRole('button', {name: `选择${short}：目标b`});
    fireEvent.click(first);
    fireEvent.click(second);
    expect(first).toHaveAttribute('aria-pressed', 'false');
    expect(second).toHaveAttribute('aria-pressed', 'true');
    expect(screen.queryByLabelText('出镜顺序 1')).not.toBeInTheDocument();
    if (kind === 'product') fireEvent.click(screen.getByRole('checkbox', {name: '手持展示产品'}));
    fireEvent.click(screen.getByRole('button', {name: '下一步：确认替换'}));
    await vi.waitFor(() => expect(mock.prepare).toHaveBeenCalledWith(expect.objectContaining({packId: 'b', extraPackIds: []})));
  });

  it('三人模板在目标人物中按点击顺序多选，取消后重排编号并按序提交', async () => {
    /* 模板声明 3 个「正面身份图」槽位 = 提示词里的 {{Mixed 2/3/4}} 三个人。
       人物包一人一包各一张正面图，所以必须选 3 个包；顺序就是编号顺序。 */
    mock.catalog.data.packs = [
      {id: 'face-a', kind: 'character', name: '人物A', referenceIds: ['face-a'], coverReferenceId: 'face-a', available: true, version: 1},
      {id: 'face-b', kind: 'character', name: '人物B', referenceIds: ['face-b'], coverReferenceId: 'face-b', available: true, version: 1},
      {id: 'face-c', kind: 'character', name: '人物C', referenceIds: ['face-c'], coverReferenceId: 'face-c', available: true, version: 1},
      {id: 'face-d', kind: 'character', name: '人物D', referenceIds: ['face-d'], coverReferenceId: 'face-d', available: true, version: 1},
      {id: 'face-e', kind: 'character', name: '人物E', referenceIds: ['face-e'], coverReferenceId: 'face-e', available: false, version: 1},
    ];
    mock.videoTemplates = [{
      id: 'tpl-face', templateKey: 'three-faces', name: '三人换脸',
      description: '', prompt: '编辑 {{Mixed 1}}，{{Mixed 2}} {{Mixed 3}} {{Mixed 4}} 依次对应三人。',
      promptSha256: 'abc', sourceVideoName: '我穿的是米兰之夜模板测试.mp4',
      sourceVideoAssetId: 'source-template', sourceVideoPath: '', sourceDurationSec: 5.31,
      aspectRatio: '9:16', packId: 'face-a', packName: '人物A', packVersion: 1,
      defaultKind: 'character',
      mediaOrder: [
        {token: '{{Mixed 1}}', role: 'source_video', type: 'video', assetId: 'source-template', name: '我穿的是米兰之夜模板测试.mp4', inPrompt: true},
        {token: '{{Mixed 2}}', role: 'front', type: 'image', assetId: 'face-a', name: '人物A', inPrompt: true},
        {token: '{{Mixed 3}}', role: 'front', type: 'image', assetId: null, name: '运行时选正面身份图', inPrompt: true},
        {token: '{{Mixed 4}}', role: 'front', type: 'image', assetId: null, name: '运行时选正面身份图', inPrompt: true},
        {token: '{{Mixed 5}}', role: 'front', type: 'image', assetId: 'auxiliary', name: '辅助正面图', inPrompt: false},
      ],
      generation: {}, rules: {referenceBindings: {'{{Mixed 1}}': 'source_video', '{{Mixed 2}}': 'front', '{{Mixed 3}}': 'front', '{{Mixed 4}}': 'front'}}, coverUrl: '', pinned: true,
      createdAt: '2026-11-10T00:00:00Z', updatedAt: '2026-11-10T00:00:00Z',
    }];
    render(<App />);
    fireEvent.click(screen.getByRole('button', {name: '选择模板：我穿的是米兰之夜模板测试.mp4'}));
    const people = screen.getByRole('region', {name: '目标人物'});
    expect(within(people).getByRole('heading', {name: '选择目标人物'})).toBeVisible();
    expect(within(people).queryByRole('combobox')).not.toBeInTheDocument();
    const card = (name: string) => within(people).getByRole('button', {name: `选择人物：${name}`});
    const expectOrder = (name: string, order: number) => expect(within(card(name)).getByLabelText(`出镜顺序 ${order}`)).toBeVisible();
    expectOrder('人物A', 1);
    expect(within(people).getByText('已选 1/3')).toBeVisible();
    expect(screen.getByRole('button', {name: '下一步：确认替换'})).toBeDisabled();
    expect(card('人物E')).toBeDisabled();
    fireEvent.click(card('人物B'));
    fireEvent.click(card('人物C'));
    expectOrder('人物B', 2);
    expectOrder('人物C', 3);
    expect(card('人物D')).toBeDisabled();
    expect(within(people).getByText('已选 3/3')).toBeVisible();
    // 取消首位后连续编号；清空全部也应保持三人模式。
    fireEvent.click(card('人物A'));
    expectOrder('人物B', 1);
    expectOrder('人物C', 2);
    expect(card('人物D')).toBeEnabled();
    fireEvent.click(card('人物B'));
    fireEvent.click(card('人物C'));
    expect(within(people).getByText('已选 0/3')).toBeVisible();
    expect(screen.getByRole('button', {name: '下一步：确认替换'})).toBeDisabled();
    fireEvent.click(card('人物C'));
    fireEvent.click(card('人物A'));
    fireEvent.click(card('人物B'));
    expectOrder('人物C', 1);
    expectOrder('人物A', 2);
    expectOrder('人物B', 3);
    await vi.waitFor(() => expect(screen.getByRole('button', {name: '下一步：确认替换'})).toBeEnabled());
    fireEvent.click(screen.getByRole('button', {name: '下一步：确认替换'}));
    await vi.waitFor(() => expect(mock.prepare).toHaveBeenCalled());
    const payload = mock.prepare.mock.calls[0][0];
    expect(payload.packId).toBe('face-c');
    expect(payload.extraPackIds).toEqual(['face-a', 'face-b']);
    expect(payload.extraPackVersions).toEqual([1, 1]);
    expect(payload.referenceIds).toEqual(['face-c', 'face-a', 'face-b']);
    expect(payload.target).toBe('人物C、人物A、人物B');
  });

  it('同一原片可有五个封装功能，选择时携带具体模板 ID', async () => {
    mock.videoTemplates = Array.from({length: 5}, (_, index) => ({
      id: `function-${index}`, templateKey: `function-${index}`, name: `封装${index}`,
      description: '', prompt: '{{Mixed 1}} {{Mixed 2}}', promptSha256: 'abc',
      sourceVideoName: '我穿的是米兰之夜模板测试.mp4', sourceVideoAssetId: 'source-template',
      sourceVideoPath: '', sourceDurationSec: 5, aspectRatio: '9:16',
      packId: 'product-pack', defaultKind: 'product', mediaOrder: [], generation: {},
      rules: {workbenchLabel: `功能${index}`}, coverUrl: '', pinned: index === 0,
      createdAt: '2026-11-09T07:00:00Z', updatedAt: '2026-11-09T07:00:00Z',
    }));
    render(<App url='/templates?sourceId=source-template&kind=scene' />);
    const picker = screen.getByLabelText('替换类型');
    expect(within(picker).getAllByRole('button')).toHaveLength(5);
    fireEvent.click(within(picker).getByRole('button', {name: '功能4'}));
    expect(screen.getByLabelText('地址')).toHaveTextContent('videoTemplateId=function-4');
    mock.prepare.mockResolvedValue({...run('prepared'), kind: 'product'});
    fireEvent.click(screen.getByRole('button', {name: '下一步：确认替换'}));
    await vi.waitFor(() => expect(mock.prepare).toHaveBeenCalledWith(expect.objectContaining({
      videoTemplateId: 'function-4', kind: 'product', replacementScope: '',
    })));
  });

  it('深链接保留选中的封装功能，仅显示它声明的范围选项', () => {
    mock.videoTemplates = ['a', 'b'].map(id => ({
      id, templateKey: id, name: id, description: '',
      prompt: '{{Mixed 1}} {{Mixed 2}} {{ReplacementScope}}', promptSha256: 'abc',
      sourceVideoName: '我穿的是米兰之夜模板测试.mp4', sourceVideoAssetId: 'source-template',
      sourceVideoPath: '', sourceDurationSec: 5, aspectRatio: '9:16', packId: 'product-pack',
      defaultKind: 'product', mediaOrder: [], generation: {},
      rules: {workbenchLabel: id, scopeOptions: id === 'b' ? ['只换前景', '全片替换'] : []},
      coverUrl: '', pinned: false, createdAt: '2026-11-09T07:00:00Z', updatedAt: '2026-11-09T07:00:00Z',
    }));
    render(<App url='/templates?sourceId=source-template&videoTemplateId=b&kind=product&packId=product-pack' />);
    expect(screen.getByRole('button', {name: 'b'})).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('checkbox', {name: '只换前景'})).toBeVisible();
    expect(screen.queryByRole('checkbox', {name: '盒内可见产品'})).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name: 'a'}));
    expect(screen.queryByRole('heading', {name: '替换范围'})).not.toBeInTheDocument();
  });
});
