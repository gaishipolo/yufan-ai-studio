import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ModelSpec } from '@/entities/model/model/types';
import type { ImageJob } from '@/entities/image/model/types';
import ImagePage from './ImagePage';

/* restoreMocks: true 会把 vi.fn 的实现也清掉，所以每个 beforeEach 都要重设 */
const { modelsMock, imagesMock, mutateMock, toastMock } = vi.hoisted(() => ({
  modelsMock: vi.fn(),
  imagesMock: vi.fn(),
  mutateMock: vi.fn(),
  toastMock: vi.fn(),
}));

vi.mock('@/entities/image/api/image.api', () => ({
  useImages: imagesMock,
  useGenerateImage: () => ({ mutate: mutateMock, isPending: false }),
}));

// imageFormSchemaFor 用真实实现：这里要验的就是参数确实来自 paramSchema
vi.mock('@/entities/model/api/model.queries', async (importOriginal) => ({
  ...((await importOriginal()) as Record<string, unknown>),
  useImageModels: modelsMock,
}));

vi.mock('@/shared/lib/toast', async (importOriginal) => ({
  ...((await importOriginal()) as Record<string, unknown>),
  toast: toastMock,
}));

const AGNES: ModelSpec = {
  id: 'agnes-image-2.5-flash',
  label: 'Agnes Image 2.5 Flash',
  capability: 'image',
  provider: 'agnes',
  description: '',
  isDefault: true,
  paramSchema: {
    size: { type: 'enum', options: ['1024x1024', '1024x1536', '1536x1024'], default: '1024x1024' },
    count: { type: 'int', min: 1, max: 1, default: 1 },
  },
};

const SEEDREAM: ModelSpec = {
  ...AGNES,
  id: 'seedream-3',
  label: 'Seedream 3',
  provider: 'echo',
  isDefault: false,
  paramSchema: {
    ...AGNES.paramSchema,
    count: { type: 'int', min: 1, max: 4, default: 1 },
  },
};

function job(overrides: Partial<ImageJob> = {}): ImageJob {
  return {
    id: 'img-1',
    name: '一只白猫',
    prompt: 'a white cat',
    size: '1024x1024',
    coverUrl: '',
    outputUrl: null,
    createdAt: '2026-11-06T01:00:00Z',
    ...overrides,
  };
}

function setImages(items: ImageJob[]) {
  imagesMock.mockReturnValue({
    data: { items, total: items.length, page: 1, pageSize: 24 },
    isLoading: false,
    isFetching: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  } as never);
}

const select = (name: string) => screen.getByRole('combobox', { name }) as HTMLSelectElement;
const optionValues = (element: HTMLSelectElement) =>
  Array.from(element.options).map((option) => option.value);

beforeEach(() => {
  modelsMock.mockReset();
  imagesMock.mockReset();
  mutateMock.mockReset();
  toastMock.mockReset();
  modelsMock.mockReturnValue({ models: [AGNES, SEEDREAM], isLoading: false, isError: false });
  setImages([]);
});

describe('ImagePage · 表单参数来自模型目录', () => {
  it('尺寸取 enum 白名单（半角 x），张数按默认模型的 max 只有 1 张', () => {
    render(<ImagePage />);
    expect(optionValues(select('尺寸'))).toEqual(['1024x1024', '1024x1536', '1536x1024']);
    expect(optionValues(select('数量'))).toEqual(['1']);
    expect(select('尺寸').value).toBe('1024x1024');
  });

  it('换模型后张数上限跟着变；旧选值失效时自动回落到新模型的默认档', async () => {
    const user = userEvent.setup();
    render(<ImagePage />);

    await user.selectOptions(select('模型'), 'seedream-3');
    expect(optionValues(select('数量'))).toEqual(['1', '2', '3', '4']);
    await user.selectOptions(select('数量'), '4');
    expect(select('数量').value).toBe('4');

    await user.selectOptions(select('模型'), 'agnes-image-2.5-flash');
    expect(optionValues(select('数量'))).toEqual(['1']);
    expect(select('数量').value).toBe('1');
  });

  it('提交使用半角尺寸，默认模型留空不传 model', async () => {
    const user = userEvent.setup();
    render(<ImagePage />);
    await user.type(screen.getByPlaceholderText(/赛博朋克街头/), '一只白猫');
    await user.click(screen.getByRole('button', { name: '开始生成' }));

    expect(mutateMock).toHaveBeenCalledTimes(1);
    expect(mutateMock.mock.calls[0][0]).toEqual({
      prompt: '一只白猫',
      size: '1024x1024',
      count: 1,
    });
  });

  it('显式改选非默认模型时才下发 model，张数带上用户所选', async () => {
    const user = userEvent.setup();
    render(<ImagePage />);
    await user.selectOptions(select('模型'), 'seedream-3');
    await user.selectOptions(select('数量'), '3');
    await user.type(screen.getByPlaceholderText(/赛博朋克街头/), '一只白猫');
    await user.click(screen.getByRole('button', { name: '开始生成' }));

    expect(mutateMock.mock.calls[0][0]).toEqual({
      prompt: '一只白猫',
      size: '1024x1024',
      count: 3,
      model: 'seedream-3',
    });
  });

  it('空提示词不提交', async () => {
    const user = userEvent.setup();
    render(<ImagePage />);
    await user.click(screen.getByRole('button', { name: '开始生成' }));
    expect(mutateMock).not.toHaveBeenCalled();
    expect(toastMock).toHaveBeenCalledWith('请先填写提示词', 'error');
  });

  it('模型目录为空时禁用提交并说明原因，不拿假参数发请求', () => {
    modelsMock.mockReturnValue({ models: [], isLoading: false, isError: false });
    render(<ImagePage />);
    expect(screen.getByText('暂无可用的生图模型')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '开始生成' })).toBeDisabled();
  });
});

describe('ImagePage · 异步状态在页面上看得见', () => {
  it('排队 / 进度 / 失败原因 / 成图地址都渲染，已完成不挂角标', () => {
    setImages([
      job({ id: 'q', name: '排队中的图', status: 'queued' }),
      job({ id: 'r', name: '生成中的图', status: 'running', progress: 42 }),
      job({
        id: 's',
        name: '成功的图',
        status: 'succeeded',
        coverUrl: 'https://vendor/signed',
        outputUrl: '/static/images/done.png',
      }),
      job({ id: 'f', name: '失败的图', status: 'failed', errorMessage: '供应商超时' }),
    ]);

    const { container } = render(<ImagePage />);

    expect(screen.getByText('排队中')).toBeInTheDocument();
    expect(screen.getByText('生成中 42%')).toBeInTheDocument();
    expect(screen.getByText('生成失败')).toBeInTheDocument();
    expect(screen.getByText('供应商超时')).toBeInTheDocument();
    // outputUrl 优先于会过期的供应商 coverUrl
    expect(container.querySelector('img[src="/static/images/done.png"]')).not.toBeNull();
    expect(container.querySelector('img[src="https://vendor/signed"]')).toBeNull();
    expect(screen.queryByText('已完成')).not.toBeInTheDocument();
  });
});
