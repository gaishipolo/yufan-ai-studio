import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from './types';

const toastMock = vi.hoisted(() => vi.fn());
vi.mock('@/shared/lib/toast', () => ({ toast: toastMock }));

const { queryClient } = await import('./query-client');

/**
 * 全局并发冲突处置。
 *
 * 后端给「版本过期」的 409 打上 `details.reason = 'state_changed'`，
 * 与「业务冲突」分开。这一层以前是空的：`isStateChanged` 定义了却零调用，
 * 冲突时页面只 toast 一句「请刷新后重试」，而数据还停在旧快照上——
 * 用户看到的仍是别人改之前的样子，只能凭猜再点一次（那一次又撞同一个 409）。
 */
describe('全局并发冲突处置', () => {
  beforeEach(() => {
    toastMock.mockClear();
    queryClient.clear();
  });

  /** 造一个必然失败的 mutation，模拟写入被服务端拒绝。 */
  function failingMutation(error: ApiError) {
    return queryClient.getMutationCache().build(queryClient, {
      mutationFn: async () => {
        throw error;
      },
    });
  }

  it('版本过期：重读权威状态，并给出明确引导', async () => {
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');

    await expect(
      failingMutation(
        new ApiError('资产已被修改，请刷新后重试', 40900, { reason: 'state_changed' }),
      ).execute(undefined),
    ).rejects.toThrow();

    // 缓存必须失效：不刷新，用户看到的还是旧快照
    expect(invalidate).toHaveBeenCalled();
    // 提示要说明「发生了什么」和「接下来怎么办」，不是一句「出错了」
    expect(toastMock).toHaveBeenCalledWith(
      expect.stringContaining('已被其他人修改'),
      'warning',
    );
    invalidate.mockRestore();
  });

  it('业务冲突：不动缓存，也不打扰用户', async () => {
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');

    await expect(
      failingMutation(new ApiError('同一分组下已有同名标签', 40900)).execute(undefined),
    ).rejects.toThrow();

    // 业务冲突刷新没用，让用户自己改名字就行
    expect(invalidate).not.toHaveBeenCalled();
    expect(toastMock).not.toHaveBeenCalled();
    invalidate.mockRestore();
  });

  it('别的失败（500）既不刷新缓存也不弹提示', async () => {
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries');

    await expect(
      failingMutation(new ApiError('服务器开小差了', 50000)).execute(undefined),
    ).rejects.toThrow();

    expect(invalidate).not.toHaveBeenCalled();
    expect(toastMock).not.toHaveBeenCalled();
    invalidate.mockRestore();
  });
});
