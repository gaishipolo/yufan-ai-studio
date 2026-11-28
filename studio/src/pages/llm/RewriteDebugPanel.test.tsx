import {QueryClient, QueryClientProvider} from '@tanstack/react-query';
import {render, screen, waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {beforeEach, describe, expect, it, vi} from 'vitest';
import {RewriteDebugPanel} from './RewriteDebugPanel';

const {optionsMock, rewriteMock, saveSettingsMock} = vi.hoisted(() => ({
  optionsMock: vi.fn(),
  rewriteMock: vi.fn(),
  saveSettingsMock: vi.fn(),
}));

vi.mock('@/entities/llm/api/llm-config.api', () => ({
  llmConfigApi: {
    debugTemplates: optionsMock,
    debugRewrite: rewriteMock,
    saveRandomSettings: saveSettingsMock,
  },
}));

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: {queries: {retry: false}, mutations: {retry: false}},
  });
  return render(
    <QueryClientProvider client={client}>
      <RewriteDebugPanel models={[{id: 'primary/qwen-plus', label: 'primary/qwen-plus · qwen'}]} />
    </QueryClientProvider>,
  );
}

const RESULT = {
  ok: true,
  requestedModel: 'primary/qwen-plus',
  error: '',
  systemPrompt: '你是提示词改写器。',
  userPayload: '{"slots":[]}',
  requestParams: {temperature: 0.3, max_tokens: 456},
  rawOutput: '{"slots":[{"key":"note","text":"雨夜"}]}',
  rewrittenTexts: {note: '雨夜'},
  finalPrompt: '主图 {{Mixed 2}}；细节 雨夜',
  referenceLibraryIds: ['v1', 'debug:hero:1'],
  textLibraryIds: [],
  compiledParams: {durationSec: 5},
  promptLength: 18,
  promptLimit: 7000,
  capability: 'video',
  llmModel: 'qwen-plus',
  llmPlatformModel: 'primary/qwen-plus',
  finishReason: 'stop',
  usage: {promptTokens: 9, completionTokens: 5, totalTokens: 14},
  latencyMs: 12,
  syntheticSlots: ['hero'],
};

beforeEach(() => {
  vi.clearAllMocks();
  optionsMock.mockResolvedValue({
    templates: [
      {key: 'demo', name: '演示模板', version: 3, status: 'published'},
    ],
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
  rewriteMock.mockResolvedValue(RESULT);
  saveSettingsMock.mockResolvedValue({
    model: 'primary/qwen-plus',
    systemPrompt: '你是提示词改写器。',
    temperature: 0.3,
    maxTokens: 456,
    extraParams: {top_p: 0.9},
  });
});

describe('随机改写调试面板', () => {
  it('列模板、预填默认 system prompt，提交参数并展示原文与最终提示词', async () => {
    const user = userEvent.setup();
    renderPanel();

    expect(await screen.findByText('演示模板（demo · v3）')).toBeInTheDocument();
    const system = screen.getByRole('textbox', {name: 'System prompt'});
    expect(system).toHaveValue('你是提示词改写器。');

    await user.selectOptions(screen.getByLabelText('模型'), 'primary/qwen-plus');
    await user.clear(screen.getByLabelText('temperature'));
    await user.clear(screen.getByLabelText('max tokens'));
    await user.type(screen.getByLabelText('temperature'), '0.3');
    await user.type(screen.getByLabelText('max tokens'), '456');
    await user.click(screen.getByText('额外参数（JSON）'));
    const extra = screen.getByLabelText('额外参数（JSON）');
    await user.click(extra);
    await user.paste('{"top_p":0.9}');
    await user.click(screen.getByRole('button', {name: /运行改写/}));

    await waitFor(() =>
      expect(rewriteMock).toHaveBeenCalledWith({
        templateKey: 'demo',
        version: 3,
        model: 'primary/qwen-plus',
        systemPrompt: '你是提示词改写器。',
        temperature: 0.3,
        maxTokens: 456,
        extraParams: {top_p: 0.9},
      }),
    );
    expect(await screen.findByText('模型原始输出')).toBeInTheDocument();
    expect(screen.getByText(RESULT.rawOutput)).toBeInTheDocument();
    expect(
      screen.getByText('最终提示词（传给生成模型之前）'),
    ).toBeInTheDocument();
    expect(screen.getByText(RESULT.finalPrompt)).toBeInTheDocument();
    expect(screen.getByText(/必填槽位用调试占位素材展开/)).toBeInTheDocument();
    expect(screen.getByText(/tokens：9 \/ 5/)).toBeInTheDocument();
  });

  it('额外参数不是合法 JSON 时不发请求', async () => {
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText('演示模板（demo · v3）');

    await user.click(screen.getByText('额外参数（JSON）'));
    const extra = screen.getByLabelText('额外参数（JSON）');
    await user.click(extra);
    await user.paste('{bad');
    await user.click(screen.getByRole('button', {name: /运行改写/}));

    expect(await screen.findByText(/不是合法 JSON/)).toBeInTheDocument();
    expect(rewriteMock).not.toHaveBeenCalled();
  });

  it('模型稿未通过校验时仍展示原始输出', async () => {
    const user = userEvent.setup();
    rewriteMock.mockResolvedValue({
      ...RESULT,
      ok: false,
      requestedModel: 'llm/default',
      error: '模型改写出来的结果格式不对：不是 JSON',
      finalPrompt: '',
    });
    renderPanel();
    await screen.findByText('演示模板（demo · v3）');

    await user.click(screen.getByRole('button', {name: /运行改写/}));

    expect(await screen.findByText(/不是 JSON/)).toBeInTheDocument();
    expect(screen.getByText(RESULT.rawOutput)).toBeInTheDocument();
    expect(
      screen.getByText('（模型稿未通过校验，没有最终提示词）'),
    ).toBeInTheDocument();
    expect(screen.queryByText(/不参与降级链/)).not.toBeInTheDocument();
  });

  it('显式模型失败时说明它不走降级链', async () => {
    const user = userEvent.setup();
    rewriteMock.mockResolvedValue({
      ...RESULT,
      ok: false,
      requestedModel: 'primary/qwen-plus',
      error: 'LLM primary 上游异常（HTTP 404）：Model not exist.',
      finalPrompt: '',
    });
    renderPanel();
    await screen.findByText('演示模板（demo · v3）');

    await user.click(screen.getByRole('button', {name: /运行改写/}));

    expect(await screen.findByText(/不参与降级链/)).toBeInTheDocument();
    expect(screen.getByText(/想测降级请把模型切到/)).toBeInTheDocument();
  });
});

describe('覆盖生产配置', () => {
  it('把当前模型 / system prompt / 温度保存为生产配置', async () => {
    const user = userEvent.setup();
    renderPanel();
    await screen.findByText('演示模板（demo · v3）');

    await user.selectOptions(screen.getByLabelText('模型'), 'primary/qwen-plus');
    await user.clear(screen.getByLabelText('temperature'));
    await user.clear(screen.getByLabelText('max tokens'));
    await user.type(screen.getByLabelText('temperature'), '0.3');
    await user.type(screen.getByLabelText('max tokens'), '456');
    await user.click(screen.getByText('额外参数（JSON）'));
    const extra = screen.getByLabelText('额外参数（JSON）');
    await user.click(extra);
    await user.paste('{"top_p":0.9}');

    await user.click(screen.getByRole('button', {name: /覆盖生产配置/}));

    await waitFor(() =>
      expect(saveSettingsMock).toHaveBeenCalledWith({
        model: 'primary/qwen-plus',
        systemPrompt: '你是提示词改写器。',
        temperature: 0.3,
        maxTokens: 456,
        extraParams: {top_p: 0.9},
      }),
    );
    expect(await screen.findByText(/已覆盖生产配置/)).toBeInTheDocument();
  });
});

describe('已保存配置的回填与恢复', () => {
  it('加载保存值；改乱后「恢复已保存」回到保存值而不是代码默认', async () => {
    optionsMock.mockResolvedValue({
      templates: [
        {key: 'demo', name: '演示模板', version: 3, status: 'published'},
      ],
      defaultSystemPrompt: '代码默认 system',
      defaultTemperature: 1,
      maxSourceChars: 6000,
      currentSettings: {
        model: 'primary/qwen-plus',
        systemPrompt: '生产保存 system',
        temperature: 0.6,
        maxTokens: 999,
        extraParams: {top_p: 0.5},
      },
      customized: true,
    });
    const user = userEvent.setup();
    renderPanel();

    await screen.findByText('演示模板（demo · v3）');
    const system = screen.getByRole('textbox', {name: 'System prompt'});
    expect(system).toHaveValue('生产保存 system');
    expect(screen.getByLabelText('temperature')).toHaveValue(0.6);
    expect(screen.getByLabelText('max tokens')).toHaveValue(999);

    await user.clear(system);
    await user.type(system, '临时改乱的内容');
    await user.click(screen.getByRole('button', {name: /恢复已保存/}));

    expect(system).toHaveValue('生产保存 system');
    expect(screen.getByLabelText('temperature')).toHaveValue(0.6);
    expect(screen.getByLabelText('max tokens')).toHaveValue(999);
  });
});
