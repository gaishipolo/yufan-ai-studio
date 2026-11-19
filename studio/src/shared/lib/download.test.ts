import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { downloadUrl } from './download';

/**
 * 下载产物。
 *
 * 起因是个真 bug：任务中心点「下载成片」会在新标签**打开视频**，而不是存盘。
 * 根因是产物地址是对象存储的预签名直链（跨源），而 `download` 属性在跨源
 * 地址上会被浏览器忽略 —— 这时候代码里那个 `target="_blank"` 就生效了。
 *
 * 现在的做法：先 `fetch` 取字节，再用 `blob:` URL 存盘（同源协议，`download`
 * 一定生效，文件名也由我们说了算）。取不到字节才退回打开新标签。
 */

/** 记录每次 click 的 anchor（jsdom 不会真的导航） */
const clicks: HTMLAnchorElement[] = [];

beforeEach(() => {
  clicks.length = 0;
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
    this: HTMLAnchorElement,
  ) {
    clicks.push(this);
  });
  /* jsdom 的 createObjectURL 不一定可用，且我们要断言它被用上 */
  Object.assign(URL, {
    createObjectURL: vi.fn(() => 'blob:mock-object-url'),
    revokeObjectURL: vi.fn(),
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const mp4 = () => new Blob(['fake-video-bytes'], { type: 'video/mp4' });

/** 手写响应对象：不依赖 jsdom 里 Response 的实现细节 */
function okResponse(blob: Blob) {
  return { ok: true, status: 200, blob: async () => blob };
}

describe('downloadUrl', () => {
  it('取到字节后用 blob: URL 存盘，不打开新标签', async () => {
    const blob = mp4();
    const fetchMock = vi.fn(async () => okResponse(blob));
    vi.stubGlobal('fetch', fetchMock);

    await downloadUrl('http://192.168.77.64:9000/bucket/a.mp4?X-Amz-Signature=1', '成片.mp4');

    expect(clicks).toHaveLength(1);
    const anchor = clicks[0];
    // blob: 是同源协议 —— 这是 download 能生效的前提
    expect(anchor.href).toBe('blob:mock-object-url');
    expect(anchor.download).toBe('成片.mp4');
    // 关键回归点：不再挂 target="_blank"，否则又变成打开新标签
    expect(anchor.target).not.toBe('_blank');
  });

  it('不带 credentials：预签名 URL 的签名在 query 里，带 cookie 会与 CORS 的凭据策略打架', async () => {
    const fetchMock = vi.fn(async () => okResponse(mp4()));
    vi.stubGlobal('fetch', fetchMock);

    await downloadUrl('http://192.168.77.64:9000/bucket/a.mp4', '成片.mp4');

    expect(fetchMock).toHaveBeenCalledWith(
      'http://192.168.77.64:9000/bucket/a.mp4',
      expect.objectContaining({ credentials: 'omit' }),
    );
  });

  it('文件名由我们决定，不取自地址末段（跨源时浏览器会忽略 download）', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => okResponse(mp4())));

    await downloadUrl('http://192.168.77.64:9000/bucket/9f2c1a.mp4?X-Amz-Signature=1', '雨夜开场.mp4');

    expect(clicks[0].download).toBe('雨夜开场.mp4');
  });

  it('取不到字节（网络断）时退回打开新标签：至少能看到产物', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch');
      }),
    );

    await downloadUrl('http://192.168.77.64:9000/bucket/a.mp4', '成片.mp4');

    expect(clicks).toHaveLength(1);
    expect(clicks[0].href).toBe('http://192.168.77.64:9000/bucket/a.mp4');
    expect(clicks[0].target).toBe('_blank');
  });

  /* 预签名会过期。过期后 fetch 拿到 403，此时不能把一个空的/错误的 blob 存成文件。 */
  it('签名过期（403）时退回打开新标签，不存一个空壳文件', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 403, blob: async () => mp4() })));

    await downloadUrl('http://192.168.77.64:9000/bucket/a.mp4', '成片.mp4');

    expect(clicks).toHaveLength(1);
    expect(clicks[0].target).toBe('_blank');
    expect(clicks[0].href).toBe('http://192.168.77.64:9000/bucket/a.mp4');
  });

  it('同源相对地址照样走同一条路（不因为跨源与否分叉）', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => okResponse(mp4())));

    await downloadUrl('/static/out/a.mp4', '成片.mp4');

    expect(clicks[0].href).toBe('blob:mock-object-url');
    expect(clicks[0].download).toBe('成片.mp4');
  });
});
