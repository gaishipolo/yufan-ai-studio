import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import RuntimeConfigSection from './RuntimeConfigSection';

const { configMock, updateMock } = vi.hoisted(() => ({
  configMock: vi.fn(),
  updateMock: vi.fn(),
}));
vi.mock('@/entities/model/api/driver.api', () => ({
  driverApi: { config: configMock, updateConfig: updateMock },
}));

function renderSection() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <RuntimeConfigSection />
    </QueryClientProvider>,
  );
}

const secret = {
  key: 'GRSAI_API_KEY',
  label: 'GRSAI 密钥',
  group: '供应商接入',
  secret: true,
  value: '',
  configured: true,
  fingerprint: 'abcd1234',
  masked: 'sk-H***cdef',
  source: 'env_file',
  editable: true,
};
const whitelist = {
  key: 'IMAGE_ASSET_ALLOWED_HOSTS',
  label: '成图 CDN 白名单',
  group: '白名单',
  secret: false,
  value: 'cos.example.com',
  configured: true,
  source: 'env_file',
  editable: true,
};
const missingRequired = {
  key: 'LIBRARY_INGEST_ALLOWED_HOSTS',
  label: '素材入库来源白名单',
  group: '白名单',
  secret: false,
  value: '',
  configured: false,
  source: 'env_file',
  editable: true,
  required: true,
};
const fromEnv = {
  key: 'AGNES_API_KEY',
  label: 'AGNES 密钥',
  group: '供应商接入',
  secret: true,
  value: '',
  configured: true,
  source: 'environment',
  editable: false,
};

describe('运维页 · 运行时配置', () => {
  beforeEach(() => {
    configMock.mockReset().mockResolvedValue({
      items: [whitelist, secret, fromEnv, missingRequired],
      groups: ['白名单', '供应商接入'],
      envFile: 'var/prod-8081.env',
    });
    updateMock.mockReset().mockResolvedValue({ updated: ['IMAGE_ASSET_ALLOWED_HOSTS'] });
  });

  it('非密钥项回填当前生效值，页面能看出现在配的是什么', async () => {
    renderSection();
    const input = await screen.findByRole('textbox', { name: '成图 CDN 白名单' });
    // 输入框先以空值渲染，当前值在 effect 里回填；不能抢在 effect 前断言。
    await waitFor(() => expect(input).toHaveValue('cos.example.com'));
    expect(screen.getByRole('button', { name: '保存配置' })).toBeDisabled();
  });

  it('密钥掩码就在输入框里，值仍为空（输入即覆盖）', async () => {
    renderSection();
    const input = await screen.findByLabelText('GRSAI 密钥');
    await waitFor(() =>
      expect(input).toHaveAttribute('placeholder', 'sk-H***cdef'),
    );
    expect(input).toHaveValue('');
  });

  it('环境变量注入的项置灰，原因在 title 里而不是常驻小字', async () => {
    renderSection();
    const input = await screen.findByLabelText('AGNES 密钥');
    await waitFor(() => expect(input).toBeDisabled());
    expect(input).toHaveAttribute('title', expect.stringContaining('进程环境变量'));
    expect(screen.queryByText(/页面改不了/)).not.toBeInTheDocument();
  });

  it('保存只提交有改动的值，空输入不把密钥清掉', async () => {
    const user = userEvent.setup();
    renderSection();
    const input = await screen.findByRole('textbox', { name: '成图 CDN 白名单' });
    await waitFor(() => expect(input).toHaveValue('cos.example.com'));
    await user.clear(input);
    await user.type(input, 'new.example.com');
    await user.click(screen.getByRole('button', { name: '保存配置' }));

    await waitFor(() => expect(updateMock).toHaveBeenCalledTimes(1));
    expect(updateMock.mock.calls[0][0]).toEqual({
      values: { IMAGE_ASSET_ALLOWED_HOSTS: 'new.example.com' },
    });
  });

  it('必填但没配的项标出来，不让人以为已经填好了', async () => {
    renderSection();
    const input = await screen.findByRole('textbox', { name: '素材入库来源白名单' });
    await waitFor(() => expect(input).toHaveAttribute('aria-invalid', 'true'));
    expect(screen.getByText('必填项当前未配置')).toBeInTheDocument();
  });

  it('没有任何改动时保存按钮禁用', async () => {
    renderSection();
    const input = await screen.findByRole('textbox', { name: '成图 CDN 白名单' });
    await waitFor(() => expect(input).toHaveValue('cos.example.com'));
    expect(screen.getByRole('button', { name: '保存配置' })).toBeDisabled();
  });
});
