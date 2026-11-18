import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { LibraryAssetOut } from '@/shared/api/schema';
import { LIBRARY_ASSET_DRAG_TYPE, type LibraryAssetDrag } from '@/shared/lib/libraryDrag';
import type { ModelSpec } from '@/entities/model/model/types';
import StudioPage from './StudioPage';

/* restoreMocks: true 会把 vi.fn 的实现也清掉，所以每个 beforeEach 都要重设 */
const {
  modelsMock,
  mutateMock,
  toastMock,
  uploadMock,
  selectionMock,
  navigateMock,
  recentTasksMock,
  libtvQueueMock,
} = vi.hoisted(() => ({
  modelsMock: vi.fn(),
  mutateMock: vi.fn(),
  toastMock: vi.fn(),
  uploadMock: vi.fn(),
  selectionMock: vi.fn(),
  navigateMock: vi.fn(),
  recentTasksMock: vi.fn(),
  libtvQueueMock: vi.fn(),
}));

/* Link 也要 mock：页头右侧「全部 →」是站内跳转，
   mock 里给个真 anchor，用例还能顺手验 href。 */
vi.mock('react-router-dom', () => ({
  useNavigate: () => navigateMock,
  Link: ({ to, children, ...rest }: { to: string; children?: ReactNode }) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock('@/entities/task/api/task.queries', () => ({
  useCreateTask: () => ({ mutate: mutateMock, isPending: false }),
  useTaskList: recentTasksMock,
}));

vi.mock('@/entities/libtv/api/libtv.queries', () => ({
  useLibtvQueue: libtvQueueMock,
}));

// 目录里的时长/比例选项用真实派生函数：要验的正是「选项来自模型 schema」
vi.mock('@/entities/model/api/model.queries', async (importOriginal) => ({
  ...((await importOriginal()) as Record<string, unknown>),
  useModels: modelsMock,
}));

vi.mock('@/entities/shot/api/shot.api', () => ({
  useShots: () => ({ data: { items: [] } }),
}));

// 参考图先传素材库再提交 id；悬浮栏的候选列表也走这条 mock
vi.mock('@/entities/library/api/library.queries', () => ({
  useUploadAsset: () => ({ mutateAsync: uploadMock, isPending: false }),
  useLibrarySelectionInfinite: selectionMock,
  useLibraryTags: () => ({ data: [] }),
  // TextRefPicker 里的 LibraryPicker 只有打开才挂载，这里备一个空结果即可
  useLibrarySelection: () => ({ data: { items: [], total: 0 }, isPending: false }),
}));

vi.mock('@/shared/access/use-access', () => ({ useCan: () => true }));

vi.mock('@/shared/lib/toast', async (importOriginal) => ({
  ...((await importOriginal()) as Record<string, unknown>),
  toast: toastMock,
}));

/**
 * 视频模型：paramSchema 只有时长与画幅两个键（与 agnes_video / echo 一致）。
 *
 * **没有 `stylePreset`** —— 这是这一轮改动的要害：以前工作台写死了
 * 「时长 / 画幅 / 风格预设」三件套，而风格预设所有模型都没声明，
 * 于是提交必定 400（`模型 ... 没有声明风格预设（stylePreset）`）。
 */
function videoModel(overrides: Partial<ModelSpec> = {}): ModelSpec {
  return {
    id: 'agnes-video-2.5-flash',
    label: 'Agnes Video 2.5 Flash',
    capability: 'video',
    provider: 'agnes_video',
    description: '720P 文生视频，4-12 秒',
    isDefault: true,
    maxReferences: 0,
    paramSchema: {
      durationSec: { type: 'int', min: 4, max: 12, step: 1, default: 5 },
      aspectRatio: { type: 'enum', options: ['9:16', '16:9', '1:1'], default: '16:9' },
    },
    ...overrides,
  };
}

const KLING = videoModel({
  id: 'kling-2.1',
  label: '可灵 2.1',
  isDefault: false,
  description: '',
  paramSchema: {
    durationSec: { type: 'int', min: 5, max: 10, step: 1, default: 5 },
    aspectRatio: { type: 'enum', options: ['9:16'], default: '9:16' },
  },
});

/** LibTV 那样声明了计费模式的模型（`drivers/libtv.py` 的真实形状）。 */
const LIBTV = videoModel({
  id: 'libtv-wan3-replacement',
  label: 'LibTV · 720P 视频替换',
  provider: 'libtv',
  isDefault: false,
  description: '',
  maxReferences: 5,
  referenceKinds: ['image', 'video', 'audio'],
  paramSchema: {
    durationSec: { type: 'int', min: 2, max: 30, step: 1, default: 5 },
    aspectRatio: { type: 'enum', options: ['9:16', '16:9', '1:1'], default: '9:16' },
    billingMode: {
      type: 'enum',
      options: ['free', 'credits'],
      default: 'free',
      label: '计费模式',
      userSelectable: true,
      description: '免费队列（较慢，不扣积分）/ 积分加速（更快，消耗账号积分）。',
    },
  },
});

function libraryAsset(id: string, name: string): LibraryAssetOut {
  return {
    id,
    name,
    kind: 'image',
    origin: 'upload',
    ownerId: 'u-1',
    visibility: 'personal',
    state: 'active',
    createdAt: '2026-11-01T00:00:00Z',
    coverUrl: `/cover/${id}.png`,
  };
}

const LIBRARY_ITEMS = [libraryAsset('lib-1', '角色参考图'), libraryAsset('lib-2', '场景参考图')];

function setSelection(items: LibraryAssetOut[] = LIBRARY_ITEMS) {
  selectionMock.mockReturnValue({
    data: { pages: [{ items, total: items.length, page: 1, pageSize: 24 }] },
    isPending: false,
    isError: false,
    hasNextPage: false,
    isFetchingNextPage: false,
    refetch: vi.fn(),
  } as never);
}

function dragData(asset: LibraryAssetDrag) {
  return {
    types: [LIBRARY_ASSET_DRAG_TYPE],
    effectAllowed: 'all',
    dropEffect: 'none',
    setData: vi.fn(),
    getData: () => JSON.stringify(asset),
  };
}

function dropOn(element: HTMLElement, asset: LibraryAssetDrag) {
  const dataTransfer = dragData(asset);
  fireEvent.dragOver(element, { dataTransfer });
  fireEvent.drop(element, { dataTransfer });
}

const prompt = () => screen.getByPlaceholderText(/超市抢购场景/);
const dropZone = () => screen.getByRole('group', { name: '参考图投放区' });
const openLibrary = () => fireEvent.click(screen.getByRole('button', { name: '素材库' }));

/** 隐藏的 file input 直接塞文件：`userEvent.upload` 会因 `display:none` 拒绝交互 */
const pick = (files: File[]) => {
  fireEvent.change(screen.getByLabelText('上传参考图'), { target: { files } });
};
const png = (name: string) => new File([new Uint8Array([1, 2, 3])], name, { type: 'image/png' });

beforeEach(() => {
  modelsMock.mockReset();
  mutateMock.mockReset();
  toastMock.mockReset();
  uploadMock.mockReset();
  selectionMock.mockReset();
  navigateMock.mockReset();
  recentTasksMock.mockReset();
  libtvQueueMock.mockReset();
  modelsMock.mockReturnValue([videoModel()]);
  recentTasksMock.mockReturnValue({ data: { items: [], total: 0 } });
  libtvQueueMock.mockReturnValue({ data: { items: [] } });
  setSelection();
});

describe('StudioPage · 参考图（与生图页同一条通道）', () => {
  it('本地上传后按顺序把素材 id 下发给任务', async () => {
    uploadMock.mockResolvedValueOnce({ id: 'a-1', name: 'a.png', previewUrl: '/p/1' });
    uploadMock.mockResolvedValueOnce({ id: 'a-2', name: 'b.png', url: '/u/2' });
    modelsMock.mockReturnValue([videoModel({ maxReferences: 2 })]);
    render(<StudioPage />);

    pick([png('a.png'), png('b.png')]);
    await screen.findByRole('button', { name: '移除参考图 2' });

    fireEvent.change(prompt(), { target: { value: '角色走进场景' } });
    fireEvent.click(screen.getByRole('button', { name: '生成视频任务' }));

    // 顺序即语义：多图合成时提示词里的「第一张」指的就是这个顺序
    expect(mutateMock.mock.calls[0][0].referenceLibraryIds).toEqual(['a-1', 'a-2']);
  });

  it('从素材库点「＋」加入，不重复上传一份', () => {
    modelsMock.mockReturnValue([videoModel({ maxReferences: 2 })]);
    render(<StudioPage />);
    openLibrary();

    fireEvent.click(screen.getByRole('button', { name: '加入参考图：角色参考图' }));
    fireEvent.change(prompt(), { target: { value: '照着这张拍' } });
    fireEvent.click(screen.getByRole('button', { name: '生成视频任务' }));

    expect(mutateMock.mock.calls[0][0].referenceLibraryIds).toEqual(['lib-1']);
    expect(uploadMock).not.toHaveBeenCalled();
  });

  it('拖进参考图区即加入，落下顺序就是提交顺序', () => {
    modelsMock.mockReturnValue([videoModel({ maxReferences: 2 })]);
    render(<StudioPage />);

    dropOn(dropZone(), { id: 'lib-2', name: '场景参考图', url: '/cover/lib-2.png' });
    dropOn(dropZone(), { id: 'lib-1', name: '角色参考图', url: '/cover/lib-1.png' });

    fireEvent.change(prompt(), { target: { value: '把角色放进场景' } });
    fireEvent.click(screen.getByRole('button', { name: '生成视频任务' }));

    expect(mutateMock.mock.calls[0][0].referenceLibraryIds).toEqual(['lib-2', 'lib-1']);
  });

  it('同一张拖两次只算一张，不白占名额', () => {
    modelsMock.mockReturnValue([videoModel({ maxReferences: 2 })]);
    render(<StudioPage />);
    const asset = { id: 'lib-1', name: '角色参考图', url: '' };

    dropOn(dropZone(), asset);
    dropOn(dropZone(), asset);

    expect(screen.getByRole('button', { name: '移除参考图 1' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '移除参考图 2' })).toBeNull();
  });

  it('上限由模型声明决定：声明 2 张就只给 2 格', async () => {
    uploadMock.mockResolvedValueOnce({ id: 'a-1', name: 'a.png', url: '/u/1' });
    uploadMock.mockResolvedValueOnce({ id: 'a-2', name: 'b.png', url: '/u/2' });
    modelsMock.mockReturnValue([videoModel({ maxReferences: 2 })]);
    render(<StudioPage />);

    expect(screen.getByText(/最多 2 张，已选 0/)).toBeInTheDocument();
    pick([png('a.png'), png('b.png')]);
    await screen.findByRole('button', { name: '移除参考图 2' });

    // 满员后不再给添加入口，拖动也接不住
    expect(screen.getByText(/最多 2 张，已选 2/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '添加' })).toBeNull();
    dropOn(dropZone(), { id: 'lib-2', name: '场景参考图', url: '' });
    expect(screen.queryByRole('button', { name: '移除参考图 3' })).toBeNull();
  });

  it('模型没声明参考素材时整块不画 —— 画了也只会让用户填完再吃一个 400', () => {
    // 后端受理期会拒：`模型 X 不支持参考素材，请换用支持参考素材的模型`。
    // 画一个上传框让用户选半天再报错，比不画更糟。
    render(<StudioPage />);
    expect(screen.queryByLabelText('上传参考图')).toBeNull();
  });

  it('换模型清空已选参考图（新模型未必支持）', async () => {
    uploadMock.mockResolvedValueOnce({ id: 'a-1', name: 'a.png', url: '/u/1' });
    modelsMock.mockReturnValue([videoModel({ maxReferences: 2 }), KLING]);
    render(<StudioPage />);

    pick([png('a.png')]);
    await screen.findByRole('button', { name: '移除参考图 1' });

    await userEvent.setup().selectOptions(screen.getAllByRole('combobox')[0], 'kling-2.1');
    expect(screen.queryByRole('button', { name: '移除参考图 1' })).toBeNull();
  });

  it('提交成功后清空参考图，不让下一个任务莫名沿用旧图', async () => {
    uploadMock.mockResolvedValueOnce({ id: 'a-1', name: 'a.png', url: '/u/1' });
    modelsMock.mockReturnValue([videoModel({ maxReferences: 2 })]);
    mutateMock.mockImplementation((_payload, options?: { onSuccess?: () => void }) => {
      options?.onSuccess?.();
    });
    render(<StudioPage />);

    pick([png('a.png')]);
    await screen.findByRole('button', { name: '移除参考图 1' });

    fireEvent.change(prompt(), { target: { value: '角色走进场景' } });
    fireEvent.click(screen.getByRole('button', { name: '生成视频任务' }));

    await waitFor(() => {
      expect(screen.queryByRole('button', { name: '移除参考图 1' })).toBeNull();
    });
    expect(navigateMock).toHaveBeenCalledWith('/tasks');
  });

  it('点缩略图能看大图：64px 上分不清角色细节', async () => {
    uploadMock.mockResolvedValueOnce({ id: 'a-1', name: 'a.png', url: '/u/1' });
    modelsMock.mockReturnValue([videoModel({ maxReferences: 2 })]);
    render(<StudioPage />);

    pick([png('a.png')]);
    const thumb = await screen.findByRole('button', { name: '预览参考图 1：a.png' });
    fireEvent.click(thumb);

    const dialog = await screen.findByRole('dialog');
    expect(dialog).toBeInTheDocument();
    expect(dialog.querySelector('img[src="/u/1"]')).not.toBeNull();
  });
});

describe('StudioPage · 表单由模型声明驱动', () => {
  it('时长档位与画幅白名单跟着模型走', async () => {
    modelsMock.mockReturnValue([videoModel(), KLING]);
    render(<StudioPage />);

    const duration = screen.getAllByRole('combobox')[1] as HTMLSelectElement;
    expect(Array.from(duration.options).map((one) => one.value)).toEqual([
      '4',
      '5',
      '6',
      '7',
      '8',
      '9',
      '10',
      '11',
      '12',
    ]);

    await userEvent.setup().selectOptions(screen.getAllByRole('combobox')[0], 'kling-2.1');
    expect(Array.from(duration.options).map((one) => one.value)).toEqual([
      '5',
      '6',
      '7',
      '8',
      '9',
      '10',
    ]);
    // 可灵只认竖屏：横屏选项不该留在界面上
    expect(
      (screen.getAllByRole('combobox')[2] as HTMLSelectElement).options,
    ).toHaveLength(1);
  });

  it('模型没声明的参数一个控件都不出现', () => {
    // 以前这里写死「时长 / 画幅 / 风格预设」三件套，其中风格预设所有模型
    // 都没声明 → 提交必定 400（`没有声明风格预设（stylePreset）`）。
    render(<StudioPage />);

    expect(screen.queryByLabelText('风格')).toBeNull();
    expect(screen.queryByRole('button', { name: '广告质感' })).toBeNull();
  });

  it('模型声明了计费模式就有控件，没声明就没有', async () => {
    modelsMock.mockReturnValue([LIBTV]);
    render(<StudioPage />);

    const billing = screen.getByLabelText('计费模式') as HTMLSelectElement;
    expect(Array.from(billing.options).map((one) => one.value)).toEqual(['free', 'credits']);
  });

  it('提交时带上用户选的参数，并按模型声明摊平', async () => {
    modelsMock.mockReturnValue([LIBTV]);
    render(<StudioPage />);

    fireEvent.change(prompt(), { target: { value: '雨夜街头' } });
    await userEvent.setup().selectOptions(screen.getByLabelText('计费模式'), 'credits');
    fireEvent.click(screen.getByRole('button', { name: '生成视频任务' }));

    expect(mutateMock.mock.calls[0][0]).toMatchObject({
      prompt: '雨夜街头',
      model: 'libtv-wan3-replacement',
      durationSec: 5,
      aspectRatio: '9:16',
      billingMode: 'credits',
    });
  });

  it('没写画面描述就不提交', () => {
    render(<StudioPage />);
    fireEvent.click(screen.getByRole('button', { name: '生成视频任务' }));

    expect(mutateMock).not.toHaveBeenCalled();
    expect(screen.getByText('请先填写画面描述')).toBeInTheDocument();
  });

  it('最近生成里同步显示 LibTV 排队位次、实时百分比与真实失败原因', () => {
    recentTasksMock.mockReturnValue({
      data: {
        items: [
          {
            id: 't-run',
            title: '免费队列测试',
            model: 'LibTV · 720P 视频替换',
            modelId: 'libtv-wan3-replacement',
            aspectRatio: '9:16',
            durationSec: 10,
            status: 'running',
            progress: 10,
            coverUrl: '',
            prompt: 'p',
            authorId: 'u_001',
            authorName: 'test',
            favorited: false,
            downloadable: false,
            createdAt: '2026-11-18T03:30:00Z',
            updatedAt: '2026-11-18T03:31:00Z',
            externalId: 'run1:20261118113003683067197',
          },
          {
            id: 't-fail',
            title: '失败原因透出',
            model: 'LibTV · 720P 视频替换',
            modelId: 'libtv-wan3-replacement',
            aspectRatio: '9:16',
            durationSec: 10,
            status: 'failed',
            progress: 70,
            coverUrl: '',
            prompt: 'p',
            authorId: 'u_001',
            authorName: 'test',
            favorited: false,
            downloadable: false,
            errorMessage: 'LibTV 生成失败：视频生成失败，积分将会在2小时内返还，请稍后重试',
            createdAt: '2026-11-18T03:30:00Z',
            updatedAt: '2026-11-18T03:44:00Z',
          },
        ],
        total: 2,
      },
    });
    libtvQueueMock.mockReturnValue({
      data: {
        items: [
          {
            taskId: '20261118113003683067197',
            status: 0,
            percent: 42,
            queueReason: 'UNLIMITED_SERIAL',
            queuePosition: 3,
            queueTotal: 5,
            queueLimit: 10,
            power: 0,
            benefitTag: '',
            remainSeconds: 0,
            describe: '0 积分队列排队中：本机 5/10（这台排第 3）· 42%',
          },
        ],
      },
    });

    render(<StudioPage />);

    expect(screen.getByText('生成中 · 42%')).toBeInTheDocument();
    expect(screen.getByText('0 积分队列 3/10')).toBeInTheDocument();
    expect(
      screen.getByText('LibTV 生成失败：视频生成失败，积分将会在2小时内返还，请稍后重试'),
    ).toBeInTheDocument();
  });
});
