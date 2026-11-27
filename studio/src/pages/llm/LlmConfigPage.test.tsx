import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import LlmConfigPage from './LlmConfigPage';

const { listMock, saveMock, removeMock, fetchMock, testMock } = vi.hoisted(() => ({
  listMock: vi.fn(),
  saveMock: vi.fn(),
  removeMock: vi.fn(),
  fetchMock: vi.fn(),
  testMock: vi.fn(),
}));
vi.mock('@/entities/llm/api/llm-config.api', () => ({
  llmConfigApi: {
    list: listMock,
    save: saveMock,
    remove: removeMock,
    fetchModels: fetchMock,
    test: testMock,
  },
}));
vi.mock('@/shared/access/use-access', () => ({ useCan: () => true }));

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
  models: [{ id: 'llm/flash', upstream: 'MiniMax-M3.1-Flash' }],
  source: 'env_file',
};

describe('LLM 配置页', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listMock.mockResolvedValue({ providers: [] });
    saveMock.mockResolvedValue(provider);
    fetchMock.mockResolvedValue({ models: [{ id: 'MiniMax-M3.1-Flash' }] });
    testMock.mockResolvedValue({ model: 'MiniMax-M3.1-Flash', text: 'OK' });
  });

  it('空态明说要填网关和密钥', async () => {
    renderPage();
    expect(await screen.findByText(/还没有供应商/)).toBeInTheDocument();
    expect(screen.getByText(/填上地址和密钥/)).toBeInTheDocument();
  });

  it('列出供应商时只显示密钥掩码，不出现明文', async () => {
    listMock.mockResolvedValue({ providers: [provider] });
    renderPage();
    expect(await screen.findByText('minimax')).toBeInTheDocument();
    expect(screen.getByText('sk-c***x5ky')).toBeInTheDocument();
    expect(screen.getByText('llm/flash')).toBeInTheDocument();
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

  it('新增供应商提交时带上模型映射', async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole('button', { name: /新增供应商/ }));
    await user.type(screen.getByLabelText('供应商名'), 'minimax');
    await user.type(screen.getByLabelText('网关地址'), 'https://api.minimaxi.com/v1');
    await user.type(screen.getByLabelText('密钥'), 'sk-secret');
    await user.click(screen.getByRole('button', { name: /加一个模型映射/ }));
    await user.type(screen.getByLabelText('平台模型 id 1'), 'llm/flash');
    await user.type(screen.getByLabelText('上游模型名 1'), 'MiniMax-M3.1-Flash');
    await user.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => expect(saveMock).toHaveBeenCalledTimes(1));
    expect(saveMock.mock.calls[0][0]).toEqual({
      name: 'minimax',
      baseUrl: 'https://api.minimaxi.com/v1',
      apiKey: 'sk-secret',
      models: [{ id: 'llm/flash', upstream: 'MiniMax-M3.1-Flash' }],
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

    expect(await screen.findByDisplayValue('llm/minimax-m2.7')).toBeInTheDocument();
    expect(screen.getByDisplayValue('MiniMax-M2.7')).toBeInTheDocument();
    expect(other).toBeDisabled();
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

  it('连通测试结果显示回复文本，不只是「成功」', async () => {
    const user = userEvent.setup();
    listMock.mockResolvedValue({ providers: [provider] });
    renderPage();
    await user.click(await screen.findByRole('button', { name: /测试/ }));
    expect(await screen.findByText(/连通正常/)).toBeInTheDocument();
    expect(screen.getByText(/回复「OK」/)).toBeInTheDocument();
  });
});
