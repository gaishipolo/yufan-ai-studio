import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ModelSpec } from '@/entities/model/model/types';
import type { ImageJob } from '@/entities/image/model/types';
import ImagePage from './ImagePage';

/* restoreMocks: true 会把 vi.fn 的实现也清掉，所以每个 beforeEach 都要重设 */
const { modelsMock, imagesMock, mutateMock, toastMock, uploadMock } = vi.hoisted(() => ({
  modelsMock: vi.fn(),
  imagesMock: vi.fn(),
  mutateMock: vi.fn(),
  toastMock: vi.fn(),
  uploadMock: vi.fn(),
}));

vi.mock('@/entities/image/api/image.api', () => ({
  useImages: imagesMock,
  useGenerateImage: () => ({ mutate: mutateMock, isPending: false }),
}));

// 参考图先传素材库再提交 id：这里只验「传上去的 id 有没有跟着请求走」
vi.mock('@/entities/library/api/library.queries', () => ({
  useUploadAsset: () => ({ mutateAsync: uploadMock, isPending: false }),
}));

// paramForm 用真实实现：这里要验的就是控件确实来自 paramSchema
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
  maxReferences: 4,
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
  // 纯文生图：界面不该出现参考图入口
  maxReferences: 0,
  paramSchema: {
    ...AGNES.paramSchema,
    count: { type: 'int', min: 1, max: 4, default: 1 },
  },
};

const RICH: ModelSpec = {
  ...AGNES,
  id: 'rich-image',
  label: 'Rich Image',
  isDefault: false,
  paramSchema: {
    ...AGNES.paramSchema,
    quality: { type: 'enum', options: ['low', 'high'], default: 'low' },
    seed: { type: 'int', min: 0, max: 2147483647 },
    watermark: { type: 'bool', default: false },
    negativePrompt: { type: 'string' },
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
  uploadMock.mockReset();
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

  it('模型多声明参数时页面自动多出控件，不需要前端改代码', () => {
    modelsMock.mockReturnValue({ models: [RICH], isLoading: false, isError: false });
    render(<ImagePage />);

    // enum → 下拉、范围过大的 int → 数字输入、bool → 开关、string → 输入框
    expect(optionValues(select('质量'))).toEqual(['low', 'high']);
    expect(screen.getByRole('spinbutton', { name: '随机种子' })).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: '水印' })).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: '反向提示词' })).toBeInTheDocument();
  });

  it('额外参数进 params，size / count 仍走顶层字段', async () => {
    const user = userEvent.setup();
    modelsMock.mockReturnValue({ models: [RICH], isLoading: false, isError: false });
    render(<ImagePage />);

    await user.type(screen.getByPlaceholderText(/赛博朋克街头/), '一只白猫');
    await user.selectOptions(select('质量'), 'high');
    await user.type(screen.getByRole('spinbutton', { name: '随机种子' }), '42');
    await user.click(screen.getByRole('checkbox', { name: '水印' }));
    await user.type(screen.getByRole('textbox', { name: '反向提示词' }), '模糊');
    await user.click(screen.getByRole('button', { name: '开始生成' }));

    expect(mutateMock.mock.calls[0][0]).toEqual({
      prompt: '一只白猫',
      size: '1024x1024',
      count: 1,
      params: { quality: 'high', seed: 42, watermark: true, negativePrompt: '模糊' },
      model: 'rich-image',
    });
  });

  it('留空的文本参数不出现在 params 里（让后端用 schema 默认值）', async () => {
    const user = userEvent.setup();
    modelsMock.mockReturnValue({ models: [RICH], isLoading: false, isError: false });
    render(<ImagePage />);

    await user.type(screen.getByPlaceholderText(/赛博朋克街头/), '一只白猫');
    await user.click(screen.getByRole('button', { name: '开始生成' }));

    const payload = mutateMock.mock.calls[0][0] as Record<string, unknown>;
    // 没填的 seed / negativePrompt 不下发；有默认值的 quality / watermark 照发，
    // 保证「界面上显示的」就是「实际用的」
    expect(payload.params).toEqual({ quality: 'low', watermark: false });
  });
});

describe('ImagePage · 参考图（图生图 / 多图合成）', () => {
  const png = (name: string) => new File([new Uint8Array([1, 2, 3])], name, { type: 'image/png' });

  /** 隐藏的 file input 直接塞文件：`userEvent.upload` 会因 `display:none` 拒绝交互 */
  const pick = (files: File[]) => {
    fireEvent.change(screen.getByLabelText('上传参考图'), { target: { files } });
  };

  it('上传后按顺序把资产 id 带给后端', async () => {
    uploadMock.mockResolvedValueOnce({ id: 'a-1', name: 'a.png', url: '/u/1' });
    uploadMock.mockResolvedValueOnce({ id: 'a-2', name: 'b.png', url: '/u/2' });
    render(<ImagePage />);

    pick([png('a.png'), png('b.png')]);
    await screen.findByRole('button', { name: '移除参考图 2' });

    fireEvent.change(screen.getByPlaceholderText(/赛博朋克街头/), {
      target: { value: '把第一个角色放进第二个场景' },
    });
    fireEvent.click(screen.getByRole('button', { name: '开始生成' }));

    // 顺序即语义：多图合成时提示词里的「第一张」指的就是这个顺序
    expect(mutateMock.mock.calls[0][0].referenceLibraryIds).toEqual(['a-1', 'a-2']);
  });

  it('模型不支持参考图时不显示入口', () => {
    modelsMock.mockReturnValue({ models: [SEEDREAM], isLoading: false, isError: false });
    render(<ImagePage />);

    expect(screen.queryByLabelText('上传参考图')).toBeNull();
    expect(screen.queryByText(/参考图/)).toBeNull();
  });

  it('到上限后不再显示添加入口', async () => {
    uploadMock.mockResolvedValueOnce({ id: 'a-1', name: 'a.png', url: '/u/1' });
    modelsMock.mockReturnValue({
      models: [{ ...AGNES, maxReferences: 1 }],
      isLoading: false,
      isError: false,
    });
    render(<ImagePage />);

    pick([png('a.png')]);
    await screen.findByRole('button', { name: '移除参考图 1' });

    expect(screen.queryByRole('button', { name: '添加' })).toBeNull();
    expect(screen.getByText(/最多 1 张/)).toBeInTheDocument();
  });

  it('换模型清空已选参考图（新模型可能根本不收）', async () => {
    uploadMock.mockResolvedValueOnce({ id: 'a-1', name: 'a.png', url: '/u/1' });
    render(<ImagePage />);

    pick([png('a.png')]);
    await screen.findByRole('button', { name: '移除参考图 1' });

    await userEvent.setup().selectOptions(select('模型'), 'seedream-3');
    expect(screen.queryByRole('button', { name: '移除参考图 1' })).toBeNull();
  });

  it('提交成功后清空参考图，不让下一张生成莫名沿用旧图', async () => {
    uploadMock.mockResolvedValueOnce({ id: 'a-1', name: 'a.png', url: '/u/1' });
    mutateMock.mockImplementation((_payload, options?: { onSuccess?: () => void }) => {
      options?.onSuccess?.();
    });
    render(<ImagePage />);

    pick([png('a.png')]);
    await screen.findByRole('button', { name: '移除参考图 1' });

    fireEvent.change(screen.getByPlaceholderText(/赛博朋克街头/), {
      target: { value: '一只白猫' },
    });
    fireEvent.click(screen.getByRole('button', { name: '开始生成' }));

    await waitFor(() => {
      expect(screen.queryByRole('button', { name: '移除参考图 1' })).toBeNull();
    });
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

  it('用过参考图的任务在卡片上标出来', () => {
    setImages([
      job({
        id: 'ref',
        name: '参考图生成',
        status: 'succeeded',
        referenceLibraryIds: ['a-1', 'a-2'],
      }),
    ]);
    render(<ImagePage />);

    // 成图上分不出「哪张是照着参考图做的」，对比时只能靠这个角标回溯
    expect(screen.getByText('参考 2')).toBeInTheDocument();
  });
});
