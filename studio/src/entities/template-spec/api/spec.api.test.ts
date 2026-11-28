import {beforeEach, describe, expect, it, vi} from 'vitest';
import {api} from '@/shared/api/http';
import {COMPILE_TIMEOUT_MS, specApi} from './spec.api';

vi.mock('@/shared/api/http', () => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}));

describe('template spec api', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.post).mockResolvedValue({});
  });

  it('compile 单独用 60s 超时，不吃全局 axios 的 20s', async () => {
    await specApi.compile('demo', {});

    expect(api.post).toHaveBeenCalledWith(
      '/template-specs/demo/compile',
      {},
      {timeout: COMPILE_TIMEOUT_MS},
    );
    expect(COMPILE_TIMEOUT_MS).toBe(60_000);
  });
});