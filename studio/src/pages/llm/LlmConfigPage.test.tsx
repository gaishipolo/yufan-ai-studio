import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import LlmConfigPage from './LlmConfigPage';

// 这页包含多个弹窗交互，并行跑全量时偶发 5s 超时；单文件仍很快。
vi.setConfig({testTimeout: 10_000});

const {
  listMock,
  saveMock,
  renameMock,
  removeMock,
  fetchMock,
  testMock,
  chainMock,
  debugTemplatesMock,
  debugRewriteMock,
} = vi.hoisted(() => ({
  listMock: vi.fn(),
  saveMock: vi.fn(),
  renameMock: vi.fn(),
  removeMock: vi.fn(),
  fetchMock: vi.fn(),
  testMock: vi.fn(),
  chainMock: vi.fn(),
  debugTemplatesMock: vi.fn(),
  debugRewriteMock: vi.fn(),
}));
vi.mock('@/entities/llm/api/llm-config.api', () => ({
  llmConfigApi: {
    list: listMock,
    save: saveMock,
    rename: renameMock,
    remove: removeMock,
    fetchModels: fetchMock,
    test: testMock,
    saveModelChain: chainMock,
    debugTemplates: debugTemplatesMock,
    debugRewrite: debugRewriteMock,
    saveRandomSettings: vi.fn().mockResolvedValue({}),
  },
}));
vi.mock('@/shared/access/use-access', () => ({ useCan: () => true }));

async function switchTab(user: ReturnType<typeof userEvent.setup>, name: string) {
  await user.click(await screen.findByRole('button', {name}));
}

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <LlmConfigPage />
    </QueryClientProvider>,
  );
}

const provider = {
  name: 'minimax',
  baseUrl: 'https://api.minimaxi.com/v1',
  apiKeyConfigured: true,
  apiKeyMasked: 'sk-c***x5ky',
  apiKeyFingerprint: '7f826811',
  models: [{ id: 'minimax/flash', upstream: 'MiniMax-M3.1-Flash' }],
  source: 'env_file',
};

describe('LLM 配置页', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listMock.mockResolvedValue({ providers: [], modelChain: [] });
    saveMock.mockResolvedValue(provider);
    renameMock.mockResolvedValue({...provider, name: 'minimax2'});
    fetchMock.mockResolvedValue({ models: [{ id: 'MiniMax-M3.1-Flash' }] });
    testMock.mockResolvedValue({ model: 'MiniMax-M3.1-Flash', text: 'OK' });
    chainMock.mockResolvedValue({ providers: [provider], modelChain: ['minimax/flash'] });
    debugTemplatesMock.mockResolvedValue({
      templates: [],
      defaultSystemPrompt: '你是提示词改写器。',
      defaultTemperature: 1,
      maxSourceChars: 6000,
      currentSettings: {
        model: 'llm/default',
        systemPrompt: '你是提示词改写器。',
        temperature: 1,
        maxTokens: 8000,
        extraParams: {},
      },
      customized: false,
    });
    debugRewriteMock.mockResolvedValue({
      ok: false,
      error: 'not run',
      systemPrompt: '',
      userPayload: '',
    });
  });

  it('空态显示还没有供应商', async () => {
    renderPage();
    expect(await screen.findByText(/还没有供应商/)).toBeInTheDocument();
  });

  it('列出供应商时只显示密钥掩码，不出现明文', async () => {
    listMock.mockResolvedValue({ providers: [provider], modelChain: [] });
    renderPage();
    expect(await screen.findByText('minimax')).toBeInTheDocument();
    expect(screen.getByText('sk-c***x5ky')).toBeInTheDocument();
    expect(screen.getByText('minimax/flash')).toBeInTheDocument();
  });

  it('展示默认模型与降级顺序并保存', async () => {
    const user = userEvent.setup();
    listMock.mockResolvedValue({
      providers: [provider],
      modelChain: ['minimax/flash'],
    });
    renderPage();
    await switchTab(user, '默认模型与降级顺序');
    expect(
      await screen.findByRole('heading', {name: '默认模型与降级顺序'}),
    ).toBeInTheDocument();
    expect(await screen.findByText('默认')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /保存降级顺序/ }));
    await waitFor(() => expect(chainMock).toHaveBeenCalledWith(['minimax/flash']));
  });

  it('模型映射解析失败时提示该供应商配置有误，不挡住其它供应商', async () => {
    listMock.mockResolvedValue({
      providers: [
        { ...provider, models: [], configError: 'LLM_MINIMAX_MODELS：不是合法 JSON' },
        {
          ...provider,
          name: 'bailian',
          models: [{ id: 'bailian/qwen', upstream: 'qwen-plus' }],
        },
      ],
      modelChain: [],
    });
    renderPage();
    expect(await screen.findByText(/模型映射配置有误/)).toBeInTheDocument();
    expect(screen.getByText(/LLM_MINIMAX_MODELS/)).toBeInTheDocument();
    expect(screen.getByText('bailian')).toBeInTheDocument();
  });

  it('降级链配置有误时显示原因', async () => {
    const user = userEvent.setup();
    listMock.mockResolvedValue({
      providers: [],
      modelChain: [],
      modelChainError: 'LLM_MODEL_CHAIN 不是合法 JSON',
    });
    renderPage();
    await switchTab(user, '默认模型与降级顺序');
    expect(await screen.findByText(/当前降级顺序配置有误/)).toBeInTheDocument();
    expect(screen.getByText(/不是合法 JSON/)).toBeInTheDocument();
  });

  it('编辑时密钥框留空 —— 掩码在占位符，不回显明文', async () => {
    const user = userEvent.setup();
    listMock.mockResolvedValue({ providers: [provider] });
    renderPage();
    await user.click(await screen.findByRole('button', { name: '编辑' }));
    const keyInput = screen.getByLabelText('密钥') as HTMLInputElement;
    expect(keyInput.value).toBe('');
    expect(keyInput.placeholder).toBe('sk-c***x5ky');
  });

  it('编辑供应商可以改名，模型 id 同步换前缀', async () => {
    const user = userEvent.setup();
    listMock.mockResolvedValue({ providers: [provider], modelChain: ['minimax/flash'] });
    renderPage();

    await user.click(await screen.findByRole('button', { name: '编辑' }));
    const nameInput = screen.getByLabelText('供应商名');
    await user.clear(nameInput);
    await user.type(nameInput, 'minimax2');
    await user.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => expect(renameMock).toHaveBeenCalledWith('minimax', 'minimax2'));
    await waitFor(() =>
      expect(saveMock).toHaveBeenCalledWith({
        name: 'minimax2',
        baseUrl: provider.baseUrl,
        apiKey: '',
        models: [{ id: 'minimax2/flash', upstream: 'MiniMax-M3.1-Flash' }],
      }),
    );
  });

  it('新增供应商提交时带上模型映射', async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole('button', { name: /新增供应商/ }));
    await user.type(screen.getByLabelText('供应商名'), 'minimax');
    await user.type(screen.getByLabelText('网关地址'), 'https://api.minimaxi.com/v1');
    await user.type(screen.getByLabelText('密钥'), 'sk-secret');
    await user.click(screen.getByRole('button', { name: /加一个模型映射/ }));
    await user.type(screen.getByLabelText('平台模型 id 1'), 'minimax/flash');
    await user.type(screen.getByLabelText('上游模型名 1'), 'MiniMax-M3.1-Flash');
    await user.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => expect(saveMock).toHaveBeenCalledTimes(1));
    expect(saveMock.mock.calls[0][0]).toEqual({
      name: 'minimax',
      baseUrl: 'https://api.minimaxi.com/v1',
      apiKey: 'sk-secret',
      models: [{ id: 'minimax/flash', upstream: 'MiniMax-M3.1-Flash' }],
    });
  });

  it('拉取的模型列在编辑弹窗里，点一下加进映射，已加的置灰', async () => {
    const user = userEvent.setup();
    fetchMock.mockResolvedValue({
      models: [{ id: 'MiniMax-M3.1-Flash' }, { id: 'MiniMax-M2.7' }],
    });
    listMock.mockResolvedValue({ providers: [provider] });
    renderPage();
    await user.click(await screen.findByRole('button', { name: '编辑' }));
    await user.click(screen.getByRole('button', { name: /从网关拉取/ }));

    const flash = await screen.findByRole('button', { name: /MiniMax-M3\.1-Flash/ });
    // provider 里已经有这条映射 → 候选置灰，不该重复加
    expect(flash).toBeDisabled();
    const other = screen.getByRole('button', { name: /MiniMax-M2\.7/ });
    expect(other).toBeEnabled();
    await user.click(other);

    expect(await screen.findByDisplayValue('minimax/minimax-m2.7')).toBeInTheDocument();
    expect(screen.getByDisplayValue('MiniMax-M2.7')).toBeInTheDocument();
    expect(other).toBeDisabled();
  });

  it('新增供应商可以直接拉取模型，不用先保存', async () => {
    const user = userEvent.setup();
    listMock.mockResolvedValue({ providers: [] });
    fetchMock.mockResolvedValue({ models: [{ id: 'MiniMax-M3.1-Flash' }] });
    renderPage();
    await user.click(await screen.findByRole('button', { name: /新增供应商/ }));
    await user.type(screen.getByLabelText('供应商名'), 'minimax');
    await user.type(screen.getByLabelText('网关地址'), 'https://api.minimaxi.com/v1');
    await user.type(screen.getByLabelText('密钥'), 'sk-secret');

    await user.click(screen.getByRole('button', { name: /从网关拉取/ }));

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith('minimax', {
        baseUrl: 'https://api.minimaxi.com/v1',
        apiKey: 'sk-secret',
      }),
    );
    expect(saveMock).not.toHaveBeenCalled();
    await user.click(
      await screen.findByRole('button', { name: /MiniMax-M3\.1-Flash/ }),
    );
    expect(
      await screen.findByDisplayValue('minimax/minimax-m3.1-flash'),
    ).toBeInTheDocument();
  });

  it('供应商名和网关地址是必填，空着不发出注定失败的请求', async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole('button', { name: /新增供应商/ }));
    expect(screen.getByLabelText('供应商名')).toBeRequired();
    expect(screen.getByLabelText('网关地址')).toBeRequired();
    await user.click(screen.getByRole('button', { name: '保存' }));
    expect(saveMock).not.toHaveBeenCalled();
  });

  it('顶部三个子页面可切换，调试页按权限出现', async () => {
    const user = userEvent.setup();
    renderPage();
    expect(
      await screen.findByRole('button', {name: '默认模型与降级顺序'}),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', {name: '供应商配置'})).toBeInTheDocument();
    expect(screen.getByRole('button', {name: '随机改写调试'})).toBeInTheDocument();

    await switchTab(user, '默认模型与降级顺序');
    expect(
      await screen.findByRole('heading', {name: '默认模型与降级顺序'}),
    ).toBeInTheDocument();

    await switchTab(user, '随机改写调试');
    expect(await screen.findByRole('heading', {name: '随机改写调试'})).toBeInTheDocument();
  });

  it('模型可设为默认，并在当前默认模型上显示徽标', async () => {
    const user = userEvent.setup();
    listMock.mockResolvedValue({
      providers: [
        {
          ...provider,
          models: [
            { id: 'minimax/flash', upstream: 'MiniMax-M3.1-Flash' },
            { id: 'minimax/m2', upstream: 'MiniMax-M2.7' },
          ],
        },
      ],
      modelChain: ['minimax/flash'],
    });
    renderPage();

    expect(await screen.findByText('默认')).toBeInTheDocument();
    expect(
      screen.queryByRole('button', {name: '将 minimax/flash 设为默认'}),
    ).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', {name: '将 minimax/m2 设为默认'}));
    await waitFor(() =>
      expect(chainMock).toHaveBeenCalledWith(['minimax/m2', 'minimax/flash']),
    );
    expect(await screen.findByText(/已将 minimax\/m2 设为默认模型/)).toBeInTheDocument();
  });

  it('连通测试结果显示回复文本，不只是「成功」', async () => {
    const user = userEvent.setup();
    listMock.mockResolvedValue({ providers: [provider] });
    renderPage();
    await user.click(await screen.findByRole('button', { name: /测试/ }));
    expect(await screen.findByText(/连通正常/)).toBeInTheDocument();
    expect(screen.getByText(/回复「OK」/)).toBeInTheDocument();
  });
});
